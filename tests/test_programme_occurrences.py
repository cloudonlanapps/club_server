"""Occurrence rules across the three types (#384 phase 0).

Programme R16–R20b, camp R81a/R81b/R82a and one-off R16–R19: cancel, undo,
reschedule, the 30-minute lead, postpone-only, the slot-decides rule, and the
forward generation window (#370).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    cancel_occurrence,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    get_event,
    list_occurrences,
    list_user_occurrences,
    mark,
    occurrence_version,
    reschedule_occurrence,
    set_cutoff,
    version_of,
)


async def _reschedule_oneoff(
    client: AsyncClient, token: str, event_id: int, new_start: int
):
    """A one-off is moved with ``/reschedule``; its occurrence endpoint refuses (#472)."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": new_start,
            "endTimeUtc": new_start + HOUR_MS,
        },
        headers=auth(token),
    )


async def _occurrence(
    client: AsyncClient, token: str, event_id: int, slot: int
) -> dict:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# R16, R17, R17a: cancelling a single occurrence
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R16")
@pytest.mark.asyncio
async def test_should_cancel_and_restore_occurrence_when_admin_acts_on_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    slot = start + DAY_MS

    cancelled = await cancel_occurrence(client, admin, programme["id"], slot)
    assert cancelled.status_code == 204, cancelled.text
    assert (await _occurrence(client, admin, programme["id"], slot))[
        "status"
    ] == "cancelled"
    mine = await list_user_occurrences(
        client,
        member,
        "skater",
        slot - HOUR_MS,
        slot + HOUR_MS,
        event_id=programme["id"],
    )
    assert [o["status"] for o in mine] == ["cancelled"]

    restored = await client.post(
        f"/v1/events/by_id/{programme['id']}/occurrences/{slot}/undo-cancel",
        json={
            "version": await occurrence_version(client, admin, programme["id"], slot)
        },
        headers=auth(admin),
    )
    assert restored.status_code == 204, restored.text
    assert (await _occurrence(client, admin, programme["id"], slot))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("programme:R17")
@pytest.mark.asyncio
async def test_should_reject_cancel_when_programme_occurrence_starts_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=20)
    programme = await create_programme(client, super_admin, venue, start=start)

    response = await cancel_occurrence(client, admin, programme["id"], start)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "CANCELLATION_LEAD_TIME_VIOLATED"
    assert (await _occurrence(client, admin, programme["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("programme:R17")
@pytest.mark.asyncio
async def test_should_cancel_when_programme_occurrence_starts_beyond_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=32)
    programme = await create_programme(client, super_admin, venue, start=start)

    response = await cancel_occurrence(client, admin, programme["id"], start)

    assert response.status_code == 204, response.text
    assert (await _occurrence(client, admin, programme["id"], start))[
        "status"
    ] == "cancelled"


@pytest.mark.requirement("programme:R17a")
@pytest.mark.asyncio
async def test_should_cancel_past_occurrence_when_caller_is_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=-3)
    programme = await create_programme(client, super_admin, venue, start=start)
    slot = start + DAY_MS

    refused = await cancel_occurrence(client, admin, programme["id"], slot)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "PAST_OCCURRENCE"

    response = await cancel_occurrence(client, super_admin, programme["id"], slot)

    assert response.status_code == 204, response.text
    assert (await _occurrence(client, admin, programme["id"], slot))[
        "status"
    ] == "cancelled"


@pytest.mark.requirement("camps:R82a")
@pytest.mark.asyncio
async def test_should_reject_cancel_when_camp_occurrence_starts_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=20)
    camp = await create_camp(client, super_admin, venue, start=start)

    response = await cancel_occurrence(client, admin, camp["id"], start)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "CANCELLATION_LEAD_TIME_VIOLATED"
    assert (await _occurrence(client, admin, camp["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("oneoff:R5")
@pytest.mark.asyncio
async def test_should_reject_drop_when_oneoff_starts_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=20)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)

    response = await drop(client, admin, oneoff["id"])

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "CANCELLATION_LEAD_TIME_VIOLATED"
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "scheduled"


@pytest.mark.requirement("oneoff:R5")
@pytest.mark.asyncio
async def test_should_drop_when_oneoff_starts_beyond_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=32)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)

    response = await drop(client, admin, oneoff["id"])

    assert response.status_code == 200, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "cancelled"


@pytest.mark.requirement("oneoff:R5a")
@pytest.mark.asyncio
async def test_should_drop_past_oneoff_when_caller_is_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(hours=-2)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)

    refused = await drop(client, admin, oneoff["id"])
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "PAST_OCCURRENCE"

    response = await drop(client, super_admin, oneoff["id"])

    assert response.status_code == 200, response.text
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "status"
    ] == "cancelled"


# ---------------------------------------------------------------------------
# R18 / #370: the forward window does not shrink with age
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R18")
@pytest.mark.asyncio
async def test_should_address_future_occurrence_when_programme_is_years_old(
    client: AsyncClient, db_session: AsyncSession
):
    """A daily programme two years old has run past any generation cap."""
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-730)
    programme = await create_programme(client, admin, venue, start=start)
    slot = start + 733 * DAY_MS

    response = await cancel_occurrence(client, admin, programme["id"], slot)

    assert response.status_code == 204, response.text
    listed = await list_occurrences(
        client, admin, slot - HOUR_MS, slot + HOUR_MS, event_id=programme["id"]
    )
    assert [o["status"] for o in listed] == ["cancelled"]


@pytest.mark.requirement("programme:R18")
@pytest.mark.asyncio
async def test_should_keep_past_occurrence_addressable_when_it_left_a_record(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-400)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    slot = start + 399 * DAY_MS
    marked = await mark(client, admin, programme["id"], slot, "skater")
    assert marked.status_code == 200, marked.text
    assert marked.json()["marked"] == [{"membername": "skater", "status": "present"}]

    response = await client.get(
        f"/v1/events/by_id/{programme['id']}/occurrences/{slot}/attendance",
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    assert [(r["membername"], r["status"]) for r in response.json()] == [
        ("skater", "present")
    ]


# ---------------------------------------------------------------------------
# R19, R19a, camp R81a, one-off R16/R16a: reschedule may only postpone
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R19")
@pytest.mark.asyncio
async def test_should_move_programme_occurrence_later_when_rescheduled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    slot = start + DAY_MS

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newStartTimeUtc=slot + 2 * HOUR_MS
    )

    assert response.status_code == 204, response.text
    occurrence = await _occurrence(client, admin, programme["id"], slot)
    assert occurrence["startTimeUtc"] == slot + 2 * HOUR_MS
    assert occurrence["isRescheduled"] is True


@pytest.mark.requirement("programme:R19a")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_programme_occurrence_moved_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    slot = start + DAY_MS

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newStartTimeUtc=slot - HOUR_MS
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "POSTPONE_ONLY"
    assert (await _occurrence(client, admin, programme["id"], slot))[
        "startTimeUtc"
    ] == slot


@pytest.mark.requirement("programme:R19a")
@pytest.mark.asyncio
async def test_should_measure_postpone_from_current_effective_start(
    client: AsyncClient, db_session: AsyncSession
):
    """Once postponed, a move back towards the slot is still a move earlier."""
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    slot = start + DAY_MS
    first = await reschedule_occurrence(
        client, admin, programme["id"], slot, newStartTimeUtc=slot + 4 * HOUR_MS
    )
    assert first.status_code == 204, first.text

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newStartTimeUtc=slot + 2 * HOUR_MS
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "POSTPONE_ONLY"
    assert (await _occurrence(client, admin, programme["id"], slot))[
        "startTimeUtc"
    ] == slot + 4 * HOUR_MS


@pytest.mark.requirement("programme:R19a")
@pytest.mark.asyncio
async def test_should_allow_venue_change_without_moving_start(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    other = await create_venue(client, super_admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    slot = start + DAY_MS

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newVenueId=other
    )

    assert response.status_code == 204, response.text
    occurrence = await _occurrence(client, admin, programme["id"], slot)
    assert occurrence["venueId"] == other
    assert occurrence["startTimeUtc"] == slot


@pytest.mark.requirement("camps:R81a")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_camp_day_moved_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    camp = await create_camp(client, super_admin, venue, start=start)
    slot = start + 2 * DAY_MS

    response = await reschedule_occurrence(
        client, admin, camp["id"], slot, newStartTimeUtc=slot - 3 * HOUR_MS
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "POSTPONE_ONLY"
    assert (await _occurrence(client, admin, camp["id"], slot))["startTimeUtc"] == slot


@pytest.mark.requirement("camps:R81b")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_camp_day_starts_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=20)
    camp = await create_camp(client, super_admin, venue, start=start)

    response = await reschedule_occurrence(
        client, admin, camp["id"], start, newStartTimeUtc=start + DAY_MS + HOUR_MS
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "RESCHEDULE_LEAD_TIME_VIOLATED"
    assert (await _occurrence(client, admin, camp["id"], start))[
        "startTimeUtc"
    ] == start


@pytest.mark.requirement("oneoff:R16")
@pytest.mark.asyncio
async def test_should_move_oneoff_later_when_rescheduled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)

    response = await _reschedule_oneoff(
        client, admin, oneoff["id"], start + 3 * HOUR_MS
    )

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, oneoff["id"]))[
        "startTimeUtc"
    ] == start + 3 * HOUR_MS


@pytest.mark.requirement("oneoff:R16a")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_oneoff_moved_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)

    response = await _reschedule_oneoff(client, admin, oneoff["id"], start - HOUR_MS)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "POSTPONE_ONLY"
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "startTimeUtc"
    ] == start


@pytest.mark.requirement("oneoff:R16b")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_oneoff_starts_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=20)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)

    response = await _reschedule_oneoff(client, admin, oneoff["id"], start + DAY_MS)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "RESCHEDULE_LEAD_TIME_VIOLATED"
    assert (await _occurrence(client, admin, oneoff["id"], start))[
        "startTimeUtc"
    ] == start


@pytest.mark.requirement("programme:R19a")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_programme_occurrence_starts_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=20)
    programme = await create_programme(client, super_admin, venue, start=start)

    response = await reschedule_occurrence(
        client, admin, programme["id"], start, newStartTimeUtc=start + DAY_MS
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "RESCHEDULE_LEAD_TIME_VIOLATED"


# ---------------------------------------------------------------------------
# R19b / #380: the slot decides whether an occurrence happens
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R19b")
@pytest.mark.asyncio
async def test_should_keep_occurrence_live_when_slot_before_cutoff_is_postponed_past_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    slot = start + 2 * DAY_MS
    moved = await reschedule_occurrence(
        client, admin, programme["id"], slot, newStartTimeUtc=cutoff + HOUR_MS
    )
    assert moved.status_code == 204, moved.text
    await set_cutoff(db_session, programme["id"], cutoff)

    listed = await list_occurrences(
        client, admin, start - HOUR_MS, start + 6 * DAY_MS, event_id=programme["id"]
    )

    by_slot = {o["occurrenceTimeUtc"]: o for o in listed}
    assert by_slot[slot]["status"] == "rescheduled"
    assert by_slot[slot]["startTimeUtc"] == cutoff + HOUR_MS


@pytest.mark.requirement("programme:R19b")
@pytest.mark.asyncio
async def test_should_show_occurrence_cancelled_when_slot_at_cutoff_was_postponed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    moved = await reschedule_occurrence(
        client, admin, programme["id"], cutoff, newStartTimeUtc=cutoff + HOUR_MS
    )
    assert moved.status_code == 204, moved.text
    await set_cutoff(db_session, programme["id"], cutoff)

    listed = await list_occurrences(
        client, admin, start - HOUR_MS, start + 6 * DAY_MS, event_id=programme["id"]
    )

    by_slot = {o["occurrenceTimeUtc"]: o for o in listed}
    assert by_slot[cutoff]["status"] == "cancelled"
    assert (await _occurrence(client, admin, programme["id"], cutoff))[
        "status"
    ] == "cancelled"


@pytest.mark.requirement("programme:R19b")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_slot_is_at_or_after_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    await set_cutoff(db_session, programme["id"], cutoff)

    response = await reschedule_occurrence(
        client,
        admin,
        programme["id"],
        cutoff + DAY_MS,
        newStartTimeUtc=cutoff + 2 * DAY_MS,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


# ---------------------------------------------------------------------------
# R20, R20b: the timetable belongs to the schedule
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R20")
@pytest.mark.asyncio
async def test_should_accept_programme_when_timetable_sums_to_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)

    programme = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        sessions=[
            {"name": "Warm-up", "periodMinutes": 20},
            {"name": "Drills", "periodMinutes": 40},
        ],
    )

    assert [s["periodMinutes"] for s in programme["sessions"]] == [20, 40]


@pytest.mark.requirement("programme:R20")
@pytest.mark.asyncio
async def test_should_reject_programme_when_timetable_does_not_sum_to_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Programme",
            "type": "programme",
            "venueId": venue,
            "startTimeUtc": start,
            "endTimeUtc": start + HOUR_MS,
            "rrule": "FREQ=WEEKLY;BYDAY=MO",
            "sessions": [{"name": "Warm-up", "periodMinutes": 20}],
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"


@pytest.mark.requirement("programme:R20b")
@pytest.mark.asyncio
async def test_should_reject_duration_change_when_schedule_carries_a_timetable(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        sessions=[{"name": "Skate", "periodMinutes": 60}],
    )
    slot = start + DAY_MS

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newDurationMinutes=45
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS"
    assert (await _occurrence(client, admin, programme["id"], slot))[
        "endTimeUtc"
    ] == slot + HOUR_MS


@pytest.mark.requirement("programme:R20b")
@pytest.mark.asyncio
async def test_should_allow_start_move_when_schedule_carries_a_timetable(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        sessions=[{"name": "Skate", "periodMinutes": 60}],
    )
    slot = start + DAY_MS

    response = await reschedule_occurrence(
        client, admin, programme["id"], slot, newStartTimeUtc=slot + 30 * MINUTE_MS
    )

    assert response.status_code == 204, response.text
    occurrence = await _occurrence(client, admin, programme["id"], slot)
    assert occurrence["startTimeUtc"] == slot + 30 * MINUTE_MS
    assert occurrence["endTimeUtc"] == slot + 30 * MINUTE_MS + HOUR_MS
