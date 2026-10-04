"""One-off lifecycle (#384 phase 0): ``docs/oneoff_requirements.md`` R1–R19
except the reschedule and conflict rules, which live with their siblings in
``test_programme_occurrences.py`` and ``test_conflict_gates.py``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    auth,
    backdate_enrollment,
    cancel_occurrence,
    cancel_series,
    create_oneoff,
    create_venue,
    drop,
    get_event,
    list_occurrences,
    list_user_occurrences,
    mark,
    reinstate,
)


async def _occurrence(
    client: AsyncClient, token: str, event_id: int, slot: int
) -> dict:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.requirement("oneoff:R1")
@pytest.mark.asyncio
async def test_should_create_oneoff_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)

    oneoff = await create_oneoff(client, coach, venue, start=start)

    fetched = await get_event(client, admin, oneoff["id"])
    assert fetched["type"] == "oneOff"
    assert fetched["organizerName"] == "coach"


@pytest.mark.requirement("oneoff:R2")
@pytest.mark.asyncio
async def test_should_update_oneoff_when_caller_is_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, coach, venue)

    response = await client.patch(
        f"/v1/events/by_id/{oneoff['id']}",
        json={
            "title": "Renamed",
            "description": "Now with a description",
            "version": 1,
        },
        headers=auth(coach),
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, oneoff["id"])
    assert (fetched["title"], fetched["description"]) == (
        "Renamed",
        "Now with a description",
    )


@pytest.mark.requirement("oneoff:R3")
@pytest.mark.asyncio
async def test_should_drop_oneoff_when_admin_supplies_reason(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    assert (await assign(client, admin, oneoff["id"], "skater")).status_code == 204

    response = await drop(client, admin, oneoff["id"], reason="Rink flooded")

    assert response.status_code == 200, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "cancelled"
    mine = await list_user_occurrences(
        client,
        member,
        "skater",
        start - HOUR_MS,
        start + HOUR_MS,
        event_id=oneoff["id"],
    )
    assert [o["status"] for o in mine] == ["cancelled"]


@pytest.mark.requirement("oneoff:R3")
@pytest.mark.asyncio
async def test_should_reject_drop_when_reason_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)

    response = await client.post(
        f"/v1/events/by_id/{oneoff['id']}/drop", json={}, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("oneoff:R3")
@pytest.mark.asyncio
async def test_should_forbid_drop_when_coach_is_not_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "bystander")
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)

    response = await drop(client, coach, oneoff["id"])

    assert response.status_code == 403, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("oneoff:R4")
@pytest.mark.asyncio
async def test_should_reject_drop_when_an_effective_time_is_supplied(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)

    via_drop = await drop(client, admin, oneoff["id"], effectiveDateTimeUtc=start)
    via_cancel = await cancel_series(client, admin, oneoff["id"], start)

    assert via_drop.status_code == 422, via_drop.text
    assert via_cancel.status_code == 422, via_cancel.text
    assert via_cancel.json()["detail"]["code"] == "INVALID_STATE"
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("oneoff:R6")
@pytest.mark.asyncio
async def test_should_reinstate_dropped_oneoff_when_it_has_not_started(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    assert (await drop(client, admin, oneoff["id"])).status_code == 200

    response = await reinstate(client, admin, oneoff["id"])

    assert response.status_code == 200, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "scheduled"
    listed = await list_occurrences(
        client, admin, start - HOUR_MS, start + HOUR_MS, event_id=oneoff["id"]
    )
    assert [o["status"] for o in listed] == ["scheduled"]


@pytest.mark.requirement("oneoff:R6")
@pytest.mark.asyncio
async def test_should_reject_reinstate_when_oneoff_is_not_dropped(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)

    response = await reinstate(client, admin, oneoff["id"])

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("oneoff:R7")
@pytest.mark.asyncio
async def test_should_reject_reinstate_when_oneoff_start_has_passed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(hours=-2)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    assert (await drop(client, admin, oneoff["id"])).status_code == 200

    response = await reinstate(client, admin, oneoff["id"])

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "cancelled"


@pytest.mark.requirement("oneoff:R8")
@pytest.mark.asyncio
async def test_should_record_drop_on_the_occurrence_not_as_a_series_end(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)

    response = await drop(client, admin, oneoff["id"], reason="Rink flooded")

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] is None
    occurrence = await _occurrence(client, admin, oneoff["id"], start)
    assert occurrence["status"] == "cancelled"
    assert occurrence["cancelReason"] == "Rink flooded"


@pytest.mark.requirement("oneoff:R9")
@pytest.mark.asyncio
async def test_should_soft_delete_and_restore_oneoff_when_admin_asks(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)

    deleted = await client.delete(
        f"/v1/events/by_id/{oneoff['id']}", headers=auth(admin)
    )
    assert deleted.status_code == 200, deleted.text
    assert (await get_event(client, admin, oneoff["id"]))["deletedAtUtc"] is not None

    restored = await client.post(
        f"/v1/events/by_id/{oneoff['id']}/restore", headers=auth(admin)
    )
    assert restored.status_code == 200, restored.text
    assert (await get_event(client, admin, oneoff["id"]))["deletedAtUtc"] is None


@pytest.mark.requirement("oneoff:R10")
@pytest.mark.asyncio
async def test_should_forbid_hard_delete_when_caller_is_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    oneoff = await create_oneoff(client, super_admin, venue)
    assert (
        await client.delete(f"/v1/events/by_id/{oneoff['id']}", headers=auth(admin))
    ).status_code == 200

    response = await client.delete(
        f"/v1/events/by_id/{oneoff['id']}/hard", headers=auth(admin)
    )

    assert response.status_code == 403, response.text
    assert (await get_event(client, super_admin, oneoff["id"]))["id"] == oneoff["id"]


@pytest.mark.requirement("oneoff:R11")
@pytest.mark.asyncio
async def test_should_hard_delete_when_caller_is_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    oneoff = await create_oneoff(client, super_admin, venue)
    assert (
        await client.delete(
            f"/v1/events/by_id/{oneoff['id']}", headers=auth(super_admin)
        )
    ).status_code == 200

    response = await client.delete(
        f"/v1/events/by_id/{oneoff['id']}/hard", headers=auth(super_admin)
    )

    assert response.status_code == 204, response.text
    gone = await client.get(
        f"/v1/events/by_id/{oneoff['id']}", headers=auth(super_admin)
    )
    assert gone.status_code == 404


@pytest.mark.requirement("oneoff:R12")
@pytest.mark.asyncio
async def test_should_keep_dropped_oneoff_listed_until_it_is_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    assert (await drop(client, admin, oneoff["id"])).status_code == 200

    still_listed = await client.get("/v1/events", headers=auth(admin))
    assert [e["id"] for e in still_listed.json()["items"]] == [oneoff["id"]]
    assert (await get_event(client, admin, oneoff["id"]))["deletedAtUtc"] is None

    deleted = await client.delete(
        f"/v1/events/by_id/{oneoff['id']}", headers=auth(admin)
    )
    assert deleted.status_code == 200, deleted.text
    gone = await client.get("/v1/events", headers=auth(admin))
    assert gone.json()["items"] == []


@pytest.mark.requirement("oneoff:R13")
@pytest.mark.requirement("oneoff:R14")
@pytest.mark.asyncio
async def test_should_reject_oneoff_when_it_carries_a_recurrence_rule(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Not a series",
            "type": "oneOff",
            "venueId": venue,
            "startTimeUtc": at(days=2),
            "endTimeUtc": at(days=2, hours=1),
            "rrule": "FREQ=WEEKLY;BYDAY=MO",
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_ONEOFF"
    listed = await client.get("/v1/events", headers=auth(admin))
    assert listed.json()["items"] == []


@pytest.mark.requirement("oneoff:R15")
@pytest.mark.asyncio
async def test_should_reject_correction_and_split_when_event_is_a_oneoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)

    corrected = await client.patch(
        f"/v1/events/by_id/{oneoff['id']}/correction",
        json={"title": "Nope", "version": 1},
        headers=auth(admin),
    )
    split = await client.patch(
        f"/v1/events/by_id/{oneoff['id']}/future",
        json={"effectiveDateTimeUtc": at(days=3), "venueId": venue, "version": 1},
        headers=auth(admin),
    )

    assert corrected.status_code == 400, corrected.text
    assert corrected.json()["detail"]["code"] == "INVALID_EVENT_TYPE"
    assert split.status_code == 400, split.text
    assert split.json()["detail"]["code"] == "INVALID_EVENT_TYPE"
    assert (await get_event(client, admin, oneoff["id"]))["title"] == oneoff["title"]


@pytest.mark.requirement("oneoff:R17")
@pytest.mark.asyncio
async def test_should_treat_drop_and_occurrence_cancel_as_one_operation(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)

    cancelled = await cancel_occurrence(client, admin, oneoff["id"], start)
    assert cancelled.status_code == 204, cancelled.text
    again = await drop(client, admin, oneoff["id"])
    assert again.status_code == 422, again.text
    assert again.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"

    back = await reinstate(client, admin, oneoff["id"])
    assert back.status_code == 200, back.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("oneoff:R18")
@pytest.mark.asyncio
async def test_should_mark_attendance_when_oneoff_occurrence_has_started(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(minutes=-10)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    assert (await assign(client, admin, oneoff["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, oneoff["id"], "skater", start - HOUR_MS)

    response = await mark(client, admin, oneoff["id"], start, "skater")

    assert response.status_code == 200, response.text
    assert response.json()["marked"] == [{"membername": "skater", "status": "present"}]
    roster = await client.get(
        f"/v1/events/by_id/{oneoff['id']}/occurrences/{start}/attendance",
        headers=auth(admin),
    )
    assert [(r["membername"], r["status"]) for r in roster.json()] == [
        ("skater", "present")
    ]


@pytest.mark.requirement("oneoff:R19")
@pytest.mark.asyncio
async def test_should_refuse_attendance_when_oneoff_is_dropped(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(minutes=-10)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)
    assert (
        await assign(client, super_admin, oneoff["id"], "skater")
    ).status_code == 204
    await backdate_enrollment(db_session, oneoff["id"], "skater", start - HOUR_MS)
    assert (await drop(client, super_admin, oneoff["id"])).status_code == 200

    response = await mark(client, super_admin, oneoff["id"], start, "skater")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"
