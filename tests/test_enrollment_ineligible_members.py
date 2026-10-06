"""Enrolled members who stop matching are kept, flagged and reported (#19).

eligibility_requirements.md R20–R24. A programme member stops matching here
the way the calendar makes them — the scan is run for a later day, when the
programme's next occurrence is a day on — or, where the API's own reading
of today is under test, by correcting their date of birth.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_day
from club_server.db.models.user import User
from club_server.services.scheduler import scan_enrollment_eligibility
from club_server.utils import now_utc_ms

from .eligibility_helpers import EIGHTEEN, FIVE, edge_births, window_on
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .redesign_helpers import (
    DAY_MS,
    EVERY_DAY,
    assign,
    at,
    auth,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    notifications_for,
    terminate,
    version_of,
)

NOTICE = "enrollment.member_ineligible"
BAND = {"minAge": FIVE, "maxAge": EIGHTEEN, "strictAge": True}


def births(start: int) -> dict[str, int]:
    """Edge birth dates of the strict window counted from ``start``'s day."""
    return edge_births(window_on(club_day(start), strict=True))


async def set_user(db: AsyncSession, name: str, **values: object) -> None:
    _ = await db.execute(update(User).where(User.username == name).values(**values))
    await db.commit()
    db.expire_all()


async def records(client: AsyncClient, token: str, event_id: int) -> dict[str, bool]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return {r["membername"]: r["eligible"] for r in response.json()["records"]}


async def scan(db: AsyncSession, now: int | None = None) -> int:
    sent = await scan_enrollment_eligibility(db, now_utc_ms() if now is None else now)
    await db.commit()
    return sent


async def programme_with(
    client: AsyncClient, db: AsyncSession, admin: str, *names: str
) -> tuple[int, int]:
    """A daily programme starting in ten days, its members born on the oldest
    day its strict band admits on the first occurrence. Returns (id, start)."""
    venue = await create_venue(client, admin)
    start = at(days=10)
    for name in names:
        _ = await create_member_user(db, name, date_of_birth=births(start)["oldest"])
    programme = await create_programme(
        client, admin, venue, start=start, rrule=EVERY_DAY, **BAND
    )
    for name in names:
        assigned = await assign(client, admin, programme["id"], name)
        assert assigned.status_code == 204, assigned.text
    return programme["id"], start


async def control_programme(client: AsyncClient, db: AsyncSession, admin: str) -> None:
    """A second, running programme whose member ``zed`` is already too old.

    It gives the scan something it must report, so a test that expects
    another event to stay silent cannot pass on a scan that does nothing.
    """
    venue = await create_venue(client, admin, "Control rink")
    start = at(days=5, hours=3)
    _ = await create_member_user(db, "zed", date_of_birth=births(start)["oldest"])
    programme = await create_programme(
        client, admin, venue, start=start, rrule=EVERY_DAY, title="Control", **BAND
    )
    assigned = await assign(client, admin, programme["id"], "zed")
    assert assigned.status_code == 204, assigned.text
    await set_user(db, "zed", date_of_birth=births(start)["too_old"])


async def reported_members(db: AsyncSession) -> list[str]:
    notices = await notifications_for(db, "admin", NOTICE)
    return sorted(n.payload["data"]["membername"] for n in notices)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R20")
async def test_should_keep_and_flag_an_enrolled_member_who_no_longer_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, start = await programme_with(client, db_session, admin, "amy", "ben")
    assert await records(client, admin, event_id) == {"amy": True, "ben": True}

    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"])

    assert await records(client, admin, event_id) == {"amy": False, "ben": True}
    listing = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments", headers=auth(admin)
    )
    assert listing.json()["enrollments"] == {"amy": "assigned", "ben": "assigned"}


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R20")
async def test_should_tell_a_member_their_own_enrolment_no_longer_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    amy = await create_member_user(db_session, "amy", gender="female")
    camp = await create_camp(client, admin, venue, gender="female")
    assigned = await assign(client, admin, camp["id"], "amy")
    assert assigned.status_code == 204, assigned.text
    url = f"/v1/myevents/by_id/amy/{camp['id']}/enrollments"
    before = await client.get(url, headers=auth(amy))
    assert before.status_code == 200, before.text
    assert before.json()["eligible"] is True

    await set_user(db_session, "amy", gender="male")

    after = await client.get(url, headers=auth(amy))
    assert after.status_code == 200, after.text
    assert after.json()["eligible"] is False
    assert after.json()["status"] == "assigned"


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R20")
async def test_should_report_a_row_that_is_not_an_enrolment_as_eligible(
    client: AsyncClient, db_session: AsyncSession
):
    """An invitation is not an enrolment: there is nobody to move yet."""
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=10)
    _ = await create_member_user(
        db_session, "amy", date_of_birth=births(start)["oldest"]
    )
    camp = await create_camp(client, admin, venue, start=start, **BAND)
    invited = await client.post(
        f"/v1/events/by_id/{camp['id']}/enrollments/invite",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )
    assert invited.status_code == 204, invited.text

    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"])

    assert await records(client, admin, camp["id"]) == {"amy": True}


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R21")
async def test_should_notify_admins_when_a_programme_member_grows_out_of_the_band(
    client: AsyncClient, db_session: AsyncSession
):
    """Nothing is changed but the day: the scan runs once the first
    occurrence has gone, so the programme is counted from the next one."""
    sudo = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "boss")
    _ = await create_coach_user(db_session, "carl")
    event_id, start = await programme_with(client, db_session, sudo, "amy", "ben")
    await set_user(
        db_session, "ben", date_of_birth=births(start)["oldest"] + 30 * DAY_MS
    )
    assert await scan(db_session) == 0

    sent = await scan(db_session, start + DAY_MS // 2)

    assert sent == 2
    for recipient in ("admin", "boss"):
        notices = await notifications_for(db_session, recipient, NOTICE)
        assert len(notices) == 1, recipient
        data = notices[0].payload["data"]
        assert data["eventId"] == event_id
        assert data["eventTitle"] == "programme under test"
        assert data["membername"] == "amy"
    assert await notifications_for(db_session, "carl", NOTICE) == []
    assert await notifications_for(db_session, "amy", NOTICE) == []


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R21")
async def test_should_notify_admins_when_a_members_gender_no_longer_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    _ = await create_member_user(db_session, "amy", gender="female")
    programme = await create_programme(
        client, admin, venue, rrule=EVERY_DAY, gender="female"
    )
    assigned = await assign(client, admin, programme["id"], "amy")
    assert assigned.status_code == 204, assigned.text
    await set_user(db_session, "amy", gender="male")

    assert await scan(db_session) == 1

    notices = await notifications_for(db_session, "admin", NOTICE)
    assert [n.payload["data"]["membername"] for n in notices] == ["amy"]
    assert await records(client, admin, programme["id"]) == {"amy": False}


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R22")
async def test_should_notify_once_about_a_programme_member_however_many_scans_run(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _, start = await programme_with(client, db_session, admin, "amy")
    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"])

    assert await scan(db_session) == 1
    assert await scan(db_session) == 0
    assert await scan(db_session, start + DAY_MS // 2) == 0

    assert len(await notifications_for(db_session, "admin", NOTICE)) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R23")
async def test_should_report_afresh_a_programme_member_who_matched_again_then_stopped(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, start = await programme_with(client, db_session, admin, "amy")
    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"])
    assert await scan(db_session) == 1

    relaxed = await client.patch(
        f"/v1/events/by_id/{event_id}/correction",
        json={
            "maxAge": {"years": 19},
            "version": await version_of(client, admin, event_id),
        },
        headers=auth(admin),
    )
    assert relaxed.status_code == 200, relaxed.text
    assert await records(client, admin, event_id) == {"amy": True}
    assert await scan(db_session) == 0

    await set_user(
        db_session, "amy", date_of_birth=births(start)["too_old"] - 400 * DAY_MS
    )
    assert await scan(db_session) == 1

    assert len(await notifications_for(db_session, "admin", NOTICE)) == 2


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R24")
@pytest.mark.parametrize("kind", ["camp", "oneOff"])
async def test_should_not_report_members_of_a_camp_or_one_off(
    client: AsyncClient, db_session: AsyncSession, kind: str
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=10)
    _ = await create_member_user(
        db_session, "amy", date_of_birth=births(start)["oldest"]
    )
    create = create_camp if kind == "camp" else create_oneoff
    event = await create(client, admin, venue, start=start, **BAND)
    assigned = await assign(client, admin, event["id"], "amy")
    assert assigned.status_code == 204, assigned.text
    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"])
    await control_programme(client, db_session, admin)

    assert await scan(db_session) == 1

    assert await records(client, admin, event["id"]) == {"amy": False}
    assert await reported_members(db_session) == ["zed"]


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R24")
async def test_should_not_report_members_of_a_programme_with_no_occurrence_left(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, start = await programme_with(client, db_session, admin, "amy")
    ended = await terminate(client, admin, event_id, start)
    assert ended.status_code == 200, ended.text
    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"] - DAY_MS)
    await control_programme(client, db_session, admin)

    assert await scan(db_session) == 1

    assert await reported_members(db_session) == ["zed"]


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R24")
async def test_should_not_report_members_of_a_soft_deleted_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, start = await programme_with(client, db_session, admin, "amy")
    deleted = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(admin))
    assert deleted.status_code in (200, 204), deleted.text
    await set_user(db_session, "amy", date_of_birth=births(start)["too_old"])
    await control_programme(client, db_session, admin)

    assert await scan(db_session) == 1

    assert await reported_members(db_session) == ["zed"]
