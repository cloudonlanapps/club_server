"""The attendance edit window closes 15 days after the session **ends** (#52).

``docs/attendance_requirements.md`` R13, R14, R15b. Each test places a
session so that ``start + 15 days`` has passed but ``end + 15 days`` has not
(or both have), which is the only place the two readings disagree.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
from club_server.db.models.event import Event
from club_server.db.models.occurrence_override import OccurrenceOverride
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    auth,
    backdate_enrollment,
    create_oneoff,
    create_venue,
    mark,
)


async def _past_session(
    client: AsyncClient,
    db_session: AsyncSession,
    super_admin: str,
    *,
    start: int,
    duration_ms: int,
) -> int:
    """A one-off with ``skater`` enrolled, moved to ``start`` in the past.

    There is no endpoint that creates a session two weeks back, so the event
    is created ahead and its times are moved in place, as the attendance
    suite does.
    """
    venue = await create_venue(client, super_admin)
    oneoff = await create_oneoff(client, super_admin, venue)
    assert (
        await assign(client, super_admin, oneoff["id"], "skater")
    ).status_code == 204
    event = (
        await db_session.execute(select(Event).where(Event.id == oneoff["id"]))
    ).scalar_one()
    event.start_time = start
    event.end_time = start + duration_ms
    await db_session.commit()
    await backdate_enrollment(db_session, oneoff["id"], "skater", start - HOUR_MS)
    return oneoff["id"]


async def _roster(
    client: AsyncClient, token: str, event_id: int, slot: int
) -> list[tuple[str, str]]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/attendance",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return [(r["membername"], r["status"]) for r in response.json()]


@pytest.mark.requirement("attendance:R13")
@pytest.mark.asyncio
async def test_should_accept_mark_when_past_start_plus_15d_but_before_end_plus_15d(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    # start + 15d was two hours ago; end + 15d is four hours away.
    start = at(days=-15, hours=-2)
    event_id = await _past_session(
        client, db_session, super_admin, start=start, duration_ms=6 * HOUR_MS
    )

    response = await mark(client, admin, event_id, start, "skater")

    assert response.status_code == 200, response.text
    assert await _roster(client, admin, event_id, start) == [("skater", "present")]


@pytest.mark.requirement("attendance:R13")
@pytest.mark.asyncio
async def test_should_refuse_mark_when_past_end_plus_15d(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    # end + 15d was an hour ago.
    start = at(days=-15, hours=-4)
    event_id = await _past_session(
        client, db_session, super_admin, start=start, duration_ms=3 * HOUR_MS
    )

    response = await mark(client, admin, event_id, start, "skater")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"
    assert await _roster(client, admin, event_id, start) == []


@pytest.mark.requirement("attendance:R15b")
@pytest.mark.asyncio
async def test_should_measure_edit_window_from_rescheduled_end_when_occurrence_moved(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    # The slot ended long ago; the occurrence was moved to a later, longer
    # session whose start + 15d has passed but whose end + 15d has not.
    slot = at(days=-20)
    event_id = await _past_session(
        client, db_session, super_admin, start=slot, duration_ms=HOUR_MS
    )
    new_start = at(days=-15, hours=-2)
    db_session.add(
        OccurrenceOverride(
            event_id=event_id,
            occurrence_time=slot,
            status="rescheduled",
            new_start_time=new_start,
            new_end_time=new_start + 6 * HOUR_MS,
        )
    )
    await db_session.commit()

    response = await mark(client, admin, event_id, slot, "skater")

    assert response.status_code == 200, response.text
    assert await _roster(client, admin, event_id, slot) == [("skater", "present")]


@pytest.mark.requirement("attendance:R14")
@pytest.mark.asyncio
async def test_should_accept_leave_approval_when_past_start_plus_15d_but_before_end_plus_15d(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    start = at(days=-15, hours=-2)
    event_id = await _past_session(
        client, db_session, super_admin, start=start, duration_ms=6 * HOUR_MS
    )
    # A leave request on record; declaring one this late has no endpoint.
    db_session.add(
        AttendanceRecord(
            event_id=event_id,
            occurrence_time_utc=start,
            membername="skater",
            status=AttendanceStatus.on_leave_requested.value,
            leave_reason="Sick",
            recorded_at=now_utc_ms(),
        )
    )
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/leave/approve",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )

    assert response.status_code == 204, response.text
    assert await _roster(client, admin, event_id, start) == [("skater", "onLeave")]
