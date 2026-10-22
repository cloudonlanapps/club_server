"""A clash on a staff change or a move is reported, and the organizer decides (#72).

The generic update's organizer / coach change, a one-off's reinstate, the
in-place reschedule of a one-off or camp, and the move of one occurrence:
each answers 409 ``SCHEDULE_CONFLICT`` with the full report and writes
nothing, unless the request carries ``acceptConflicts: true`` — then it is
written and the admins are told (programme R31d, one-off R21).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    at,
    auth,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    get_event,
    notifications_for,
    reinstate,
    reschedule_occurrence,
    version_of,
    weekday_code,
)

SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"


async def _conflict_notices(db_session: AsyncSession) -> list:
    return await notifications_for(db_session, "admin", "event.conflict_detected")


async def _occurrence(client: AsyncClient, token: str, event_id: int, slot: int):
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Generic update: organizer / coach change
# ---------------------------------------------------------------------------


async def _busy_coach(client: AsyncClient, db_session: AsyncSession):
    """``bob`` coaches a one-off at Rink A; another, at Rink B, has no coach."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "bob")
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=3)
    held = await create_oneoff(client, admin, venue, start=start, coachNames=["bob"])
    edited = await create_oneoff(
        client, admin, other, start=start, organizerName="org_b"
    )
    return admin, held, edited


async def _patch(client: AsyncClient, token: str, event_id: int, **body: object):
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}", json=body, headers=auth(token)
    )


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.asyncio
async def test_should_answer_409_and_keep_staff_when_coach_change_clashes(
    client: AsyncClient, db_session: AsyncSession
):
    admin, held, edited = await _busy_coach(client, db_session)

    response = await _patch(client, admin, edited["id"], coachNames=["bob"])

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert [c["eventId"] for c in detail["coachConflicts"]] == [held["id"]]
    assert (await get_event(client, admin, edited["id"]))["coachNames"] in (None, [])
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.asyncio
async def test_should_update_and_notify_admins_when_coach_clash_is_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    admin, held, edited = await _busy_coach(client, db_session)

    response = await _patch(
        client, admin, edited["id"], coachNames=["bob"], acceptConflicts=True
    )

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, edited["id"]))["coachNames"] == ["bob"]
    notices = await _conflict_notices(db_session)
    assert [n.payload["data"]["eventId"] for n in notices] == [edited["id"]]
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        held["id"]
    ]


# ---------------------------------------------------------------------------
# One-off reinstate
# ---------------------------------------------------------------------------


async def _dropped_under_a_clash(client: AsyncClient, db_session: AsyncSession):
    """A dropped one-off whose slot another one-off has since taken."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    start = at(days=3)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    assert (await drop(client, admin, oneoff["id"])).status_code == 200
    taken = await create_oneoff(
        client, admin, venue, start=start, organizerName="org_b"
    )
    assert await _conflict_notices(db_session) == []
    return admin, start, oneoff, taken


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.asyncio
async def test_should_answer_409_and_stay_dropped_when_reinstate_clashes(
    client: AsyncClient, db_session: AsyncSession
):
    admin, start, oneoff, taken = await _dropped_under_a_clash(client, db_session)

    response = await reinstate(client, admin, oneoff["id"])

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert [c["eventId"] for c in detail["venueConflicts"]] == [taken["id"]]
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "cancelled"
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.asyncio
async def test_should_reinstate_and_notify_admins_when_reinstate_clash_is_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    admin, start, oneoff, taken = await _dropped_under_a_clash(client, db_session)

    response = await reinstate(client, admin, oneoff["id"], acceptConflicts=True)

    assert response.status_code == 200, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] != "cancelled"
    notices = await _conflict_notices(db_session)
    assert [n.payload["data"]["eventId"] for n in notices] == [oneoff["id"]]
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        taken["id"]
    ]


# ---------------------------------------------------------------------------
# In-place reschedule: one-off and camp
# ---------------------------------------------------------------------------


async def _movable(client: AsyncClient, db_session: AsyncSession, event_type: str):
    """A one-off or camp at a quiet rink, alongside a programme at a busy one."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "org_b")
    busy = await create_venue(client, admin)
    quiet = await create_venue(client, admin, "Quiet")
    start = at(days=3)
    occupied = await create_programme(
        client,
        admin,
        busy,
        start=start,
        end=start + HOUR_MS,
        rrule=f"FREQ=WEEKLY;BYDAY={weekday_code(start)}",
        organizerName="org_b",
    )
    if event_type == "camp":
        moved = await create_camp(client, admin, quiet, start=start, count=3)
    else:
        moved = await create_oneoff(client, admin, quiet, start=start)
    return admin, busy, quiet, occupied, moved


async def _reschedule(client: AsyncClient, token: str, event_id: int, **body):
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.post(
        f"/v1/events/by_id/{event_id}/reschedule", json=body, headers=auth(token)
    )


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.parametrize("event_type", ["oneOff", "camp"])
@pytest.mark.asyncio
async def test_should_answer_409_and_stay_put_when_reschedule_clashes(
    client: AsyncClient, db_session: AsyncSession, event_type: str
):
    admin, busy, quiet, occupied, moved = await _movable(client, db_session, event_type)

    response = await _reschedule(client, admin, moved["id"], venueId=busy)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert [c["eventId"] for c in detail["venueConflicts"]] == [occupied["id"]]
    assert (await get_event(client, admin, moved["id"]))["venueId"] == quiet
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.parametrize("event_type", ["oneOff", "camp"])
@pytest.mark.asyncio
async def test_should_reschedule_and_notify_admins_when_reschedule_clash_is_accepted(
    client: AsyncClient, db_session: AsyncSession, event_type: str
):
    admin, busy, _, occupied, moved = await _movable(client, db_session, event_type)

    response = await _reschedule(
        client, admin, moved["id"], venueId=busy, acceptConflicts=True
    )

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, moved["id"]))["venueId"] == busy
    notices = await _conflict_notices(db_session)
    assert [n.payload["data"]["eventId"] for n in notices] == [moved["id"]]
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        occupied["id"]
    ]


# ---------------------------------------------------------------------------
# One occurrence moved (programme R31a)
# ---------------------------------------------------------------------------


async def _occurrence_move(client: AsyncClient, db_session: AsyncSession):
    """A daily programme at Rink A; another, run by someone else, at Rink B."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=3)
    programme = await create_programme(
        client, admin, venue, start=start, end=start + HOUR_MS
    )
    occupied = await create_programme(
        client, admin, other, start=start, end=start + HOUR_MS, organizerName="org_b"
    )
    return admin, venue, other, start + DAY_MS, programme, occupied


@pytest.mark.requirement("programme:R31a")
@pytest.mark.asyncio
async def test_should_answer_409_and_leave_occurrence_when_its_move_clashes(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue, other, slot, programme, occupied = await _occurrence_move(
        client, db_session
    )

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newVenueId=other
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert [c["eventId"] for c in detail["venueConflicts"]] == [occupied["id"]]
    after = await _occurrence(client, admin, programme["id"], slot)
    assert after["venueId"] == venue
    assert after["isRescheduled"] is False
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("programme:R31a")
@pytest.mark.asyncio
async def test_should_move_occurrence_and_notify_admins_when_clash_is_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, other, slot, programme, occupied = await _occurrence_move(
        client, db_session
    )

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newVenueId=other, acceptConflicts=True
    )

    assert response.status_code == 204, response.text
    assert (await _occurrence(client, admin, programme["id"], slot))["venueId"] == other
    notices = await _conflict_notices(db_session)
    assert [n.payload["data"]["eventId"] for n in notices] == [programme["id"]]
    assert notices[0].payload["data"]["occurrenceTimeUtc"] == slot
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        occupied["id"]
    ]
