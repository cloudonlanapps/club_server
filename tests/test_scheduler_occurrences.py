"""Reminder scans work per occurrence, not per event (#475).

``Event.start_time`` is only the schedule's first slot, so a scan that
filters on it reminds members of a programme's first session and never
again. These pin that both reminder scans expand occurrences from the
schedule for their window, and that the upcoming scan stays silent for an
occurrence that is cancelled — by override or by the event's cutoff —
through ``services/lifecycle.py`` (L14).
"""

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.enrollment import Enrollment, EnrollmentStatus
from club_server.db.models.event import Event
from club_server.db.models.notification import Notification
from club_server.db.models.occurrence_override import OccurrenceOverride
from club_server.db.models.venue import Venue
from club_server.services.scheduler import scan_pending_marks, scan_upcoming_events

from .helpers import create_admin_user, create_member_user

HOUR_MS = 60 * 60 * 1000
WEEK_MS = 7 * 24 * HOUR_MS
WEEKLY = "FREQ=WEEKLY"


def _now() -> int:
    """Now, on a whole second: rule expansion is second-granular."""
    return int(datetime.now(timezone.utc).timestamp()) * 1000


async def _event(
    db_session: AsyncSession, *, start: int, rrule: str | None = None
) -> Event:
    venue = Venue(name="Scan Venue", created_at=0, updated_at=0)
    db_session.add(venue)
    await db_session.flush()
    event = Event(
        title="Practice",
        type="programme",
        visibility="public",
        venue_id=venue.id,
        start_time=start,
        end_time=start + HOUR_MS,
        rrule=rrule,
        created_at=start - 1000,
        updated_at=start - 1000,
    )
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        Enrollment(
            event_id=event.id,
            membername="amy",
            status=EnrollmentStatus.assigned.value,
            is_trial=0,
            created_at=0,
            updated_at=0,
            enrolled_at=0,
        )
    )
    await db_session.flush()
    return event


async def _payloads(
    db_session: AsyncSession, username: str, kind: str
) -> list[dict[str, Any]]:
    db_session.expire_all()
    result = await db_session.execute(
        select(Notification.payload).where(
            Notification.username == username, Notification.type == kind
        )
    )
    return [row["data"] for (row,) in result.all()]


@pytest.mark.requirement("notifications:R85")
@pytest.mark.asyncio
async def test_should_remind_members_when_a_later_occurrence_starts_within_lead(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "amy")
    now = _now()
    next_slot = now + 23 * HOUR_MS
    event = await _event(db_session, start=next_slot - WEEK_MS, rrule=WEEKLY)
    event_id = event.id
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()

    assert sent == 1
    data = await _payloads(db_session, "amy", "event.upcoming_reminder")
    assert [(d["eventId"], d["occurrenceTimeUtc"], d["leadHours"]) for d in data] == [
        (event_id, next_slot, 24)
    ]


@pytest.mark.requirement("notifications:R87")
@pytest.mark.asyncio
async def test_should_not_remind_members_when_upcoming_occurrence_is_cancelled(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "amy")
    now = _now()
    next_slot = now + 23 * HOUR_MS
    event = await _event(db_session, start=next_slot - WEEK_MS, rrule=WEEKLY)
    db_session.add(
        OccurrenceOverride(
            event_id=event.id, occurrence_time=next_slot, status="cancelled"
        )
    )
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()

    assert sent == 0
    assert await _payloads(db_session, "amy", "event.upcoming_reminder") == []


@pytest.mark.requirement("notifications:R87")
@pytest.mark.asyncio
async def test_should_not_remind_members_when_one_off_was_dropped_by_its_cutoff(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "amy")
    now = _now()
    start = now + 23 * HOUR_MS
    event = await _event(db_session, start=start)
    event.until_time = start
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()

    assert sent == 0
    assert await _payloads(db_session, "amy", "event.upcoming_reminder") == []


@pytest.mark.requirement("notifications:R87")
@pytest.mark.asyncio
async def test_should_remind_members_when_a_later_occurrence_is_cancelled_instead(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "amy")
    now = _now()
    next_slot = now + 23 * HOUR_MS
    event = await _event(db_session, start=next_slot - WEEK_MS, rrule=WEEKLY)
    db_session.add(
        OccurrenceOverride(
            event_id=event.id,
            occurrence_time=next_slot + WEEK_MS,
            status="cancelled",
        )
    )
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()

    assert sent == 1
    data = await _payloads(db_session, "amy", "event.upcoming_reminder")
    assert [d["occurrenceTimeUtc"] for d in data] == [next_slot]


@pytest.mark.requirement("notifications:R92")
@pytest.mark.asyncio
async def test_should_remind_staff_when_a_later_occurrence_ended_unmarked(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = _now()
    last_slot = now - 3 * HOUR_MS
    event = await _event(db_session, start=last_slot - WEEK_MS, rrule=WEEKLY)
    event_id = event.id
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()

    assert sent == 1
    data = await _payloads(db_session, "admin", "attendance.pending_mark_reminder")
    assert [
        (d["eventId"], d["occurrenceTimeUtc"], d["occurrenceEndTimeUtc"]) for d in data
    ] == [(event_id, last_slot, last_slot + HOUR_MS)]
