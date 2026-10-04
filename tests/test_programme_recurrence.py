"""Programme recurrence shape (#384 phase 0): R11–R15, and the rule that no
caller may write ``UNTIL`` for any type (#388).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user
from .redesign_helpers import (
    DAY_MS,
    at,
    auth,
    create_camp,
    create_event,
    create_venue,
    get_event,
    occurrence_version,
    split,
    version_of,
)


async def _create_programme_with_rule(
    client: AsyncClient, token: str, venue_id: int, rrule: str
):
    return await client.post(
        "/v1/events",
        json={
            "title": "Programme",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": at(days=2),
            "endTimeUtc": at(days=2, hours=1),
            "rrule": rrule,
        },
        headers=auth(token),
    )


def _assert_programme_rule_rejected(response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_PROGRAMME"


@pytest.mark.requirement("programme:R11")
@pytest.mark.asyncio
async def test_should_reject_programme_when_rule_carries_an_interval(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO;INTERVAL=2"
    )

    _assert_programme_rule_rejected(response)


@pytest.mark.requirement("programme:R11")
@pytest.mark.asyncio
async def test_should_reject_programme_when_rule_names_no_weekday(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(client, admin, venue, "FREQ=WEEKLY")

    _assert_programme_rule_rejected(response)


@pytest.mark.requirement("programme:R11")
@pytest.mark.asyncio
async def test_should_accept_programme_when_rule_is_weekly_with_days(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO,WE"
    )

    assert response.status_code == 201, response.text
    fetched = await get_event(client, admin, response.json()["id"])
    assert fetched["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE"


@pytest.mark.requirement("programme:R12")
@pytest.mark.asyncio
async def test_should_reject_programme_when_caller_supplies_until(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO;UNTIL=20991231T000000Z"
    )

    _assert_programme_rule_rejected(response)


@pytest.mark.requirement("programme:R12")
@pytest.mark.asyncio
async def test_should_create_programme_open_ended_when_no_bound_is_given(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO"
    )

    assert response.status_code == 201, response.text
    fetched = await get_event(client, admin, response.json()["id"])
    assert fetched["untilTimeUtc"] is None


@pytest.mark.requirement("programme:R13")
@pytest.mark.asyncio
async def test_should_reject_programme_when_rule_carries_a_count(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO;COUNT=10"
    )

    _assert_programme_rule_rejected(response)


@pytest.mark.requirement("programme:R14")
@pytest.mark.asyncio
async def test_should_reject_programme_when_rule_carries_exception_dates(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO\nEXDATE:20300107T090000Z"
    )

    _assert_programme_rule_rejected(response)


@pytest.mark.requirement("programme:R15")
@pytest.mark.asyncio
async def test_should_reject_programme_when_rule_is_not_weekly(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    daily = await _create_programme_with_rule(client, admin, venue, "FREQ=DAILY")
    monthly = await _create_programme_with_rule(
        client, admin, venue, "FREQ=MONTHLY;BYMONTHDAY=1"
    )

    _assert_programme_rule_rejected(daily)
    _assert_programme_rule_rejected(monthly)


@pytest.mark.requirement("programme:R15")
@pytest.mark.asyncio
async def test_should_reject_programme_rule_when_split_supplies_bad_shape(
    client: AsyncClient, db_session: AsyncSession
):
    """The split writes a schedule too, so the same validation applies there."""
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    created = await _create_programme_with_rule(
        client, admin, venue, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU"
    )
    assert created.status_code == 201, created.text
    start = created.json()["startTimeUtc"]

    response = await split(
        client,
        admin,
        created.json()["id"],
        effectiveDateTimeUtc=start + 7 * DAY_MS,
        rrule="FREQ=DAILY;COUNT=3",
    )

    _assert_programme_rule_rejected(response)


# ---------------------------------------------------------------------------
# #388: UNTIL is written only by terminate, split and cancel — never by a caller
# ---------------------------------------------------------------------------


@pytest.mark.requirement("camps:R2b")
@pytest.mark.asyncio
async def test_should_reject_camp_when_caller_supplies_until(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await create_event(
        client,
        admin,
        event_type="camp",
        venue_id=venue,
        start=at(days=2),
        rrule="FREQ=DAILY;UNTIL=20991231T000000Z",
        expected_status=422,
    )

    assert response["detail"]["code"] == "INVALID_RRULE_FOR_CAMP"


@pytest.mark.requirement("camps:R2b")
@pytest.mark.asyncio
async def test_should_reject_camp_reschedule_when_rule_supplies_until(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, start=at(days=2))

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/reschedule",
        json={
            "version": await version_of(client, admin, camp["id"]),
            "rrule": "FREQ=DAILY;UNTIL=20991231T000000Z",
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_CAMP"
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["rrule"] == "FREQ=DAILY;COUNT=5"


@pytest.mark.requirement("camps:R2b")
@pytest.mark.asyncio
async def test_should_derive_camp_end_from_count_when_camp_is_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start, count=3)

    schedules = await client.get(
        f"/v1/events/by_id/{camp['id']}/schedules", headers=auth(admin)
    )

    assert schedules.status_code == 200, schedules.text
    assert len(schedules.json()) == 1
    assert schedules.json()[0]["effectiveUntilUtc"] is None
    assert schedules.json()[0]["rrule"] == "FREQ=DAILY;COUNT=3"
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["untilTimeUtc"] is None


# ---------------------------------------------------------------------------
# Camp R2c: the schedule freezes once something is recorded against it
# ---------------------------------------------------------------------------


@pytest.mark.requirement("camps:R2c")
@pytest.mark.asyncio
async def test_should_freeze_camp_schedule_when_an_override_exists(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_admin_user as _admin

    admin = await _admin(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    cancelled = await client.post(
        f"/v1/events/by_id/{camp['id']}/occurrences/{start + DAY_MS}/cancel",
        json={
            "version": await occurrence_version(
                client, admin, camp["id"], start + DAY_MS
            ),
            "reason": "Ice not ready",
        },
        headers=auth(admin),
    )
    assert cancelled.status_code == 204, cancelled.text

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/reschedule",
        json={
            "version": await version_of(client, admin, camp["id"]),
            "startTimeUtc": start + 2 * DAY_MS,
        },
        headers=auth(admin),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "OCCURRENCE_OVERRIDES_PRESENT"
    assert (await get_event(client, admin, camp["id"]))["startTimeUtc"] == start


@pytest.mark.requirement("camps:R2c")
@pytest.mark.asyncio
async def test_should_freeze_camp_schedule_when_attendance_exists(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_admin_user as _admin, create_member_user
    from .redesign_helpers import HOUR_MS, assign, backdate_enrollment, mark

    admin = await _admin(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(minutes=-10)
    camp = await create_camp(client, admin, venue, start=start)
    assert (await assign(client, admin, camp["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, camp["id"], "skater", start - HOUR_MS)
    marked = await mark(client, admin, camp["id"], start, "skater")
    assert marked.status_code == 200, marked.text

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/reschedule",
        json={"version": await version_of(client, admin, camp["id"]), "venueId": venue},
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "EVENT_ALREADY_STARTED"
