"""Tests for #66 — scheduler scans: upcoming, pending-mark, absence-streak.

Each scan is unit-tested by calling it directly with a fixed ``now``,
so we exercise the SQL queries and idempotency logic without spinning
up the asyncio loop.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
from club_server.db.models.enrollment import Enrollment, EnrollmentStatus
from club_server.db.models.event import Event
from club_server.db.models.notification import Notification
from club_server.db.models.occurrence_override import OccurrenceOverride
from club_server.db.models.venue import Venue
from club_server.services.scheduler import (
    scan_absence_streaks,
    scan_pending_marks,
    scan_upcoming_events,
)

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_ms(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


async def _notifications_for(
    db_session: AsyncSession, username: str, event_type: str
) -> list[Notification]:
    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.username == username,
            Notification.type == event_type,
        )
    )
    return list(result.scalars().all())


async def _ensure_venue(db_session: AsyncSession) -> int:
    """Insert a shared venue row and return its id."""
    venue = Venue(name="Test Venue", created_at=0, updated_at=0)
    db_session.add(venue)
    await db_session.flush()
    return venue.id


async def _make_event(
    db_session: AsyncSession,
    *,
    start: int,
    end: int,
    title: str = "Practice",
    event_id: int | None = None,
    venue_id: int | None = None,
) -> Event:
    if venue_id is None:
        venue_id = await _ensure_venue(db_session)
    kwargs: dict = {
        "title": title,
        "type": "programme",
        "visibility": "public",
        "venue_id": venue_id,
        "start_time": start,
        "end_time": end,
        "created_at": start - 1000,
        "updated_at": start - 1000,
    }
    if event_id is not None:
        kwargs["id"] = event_id
    event = Event(**kwargs)
    db_session.add(event)
    await db_session.flush()
    return event


async def _enroll(db_session: AsyncSession, event_id: int, username: str) -> None:
    e = Enrollment(
        event_id=event_id,
        membername=username,
        status=EnrollmentStatus.assigned.value,
        is_trial=0,
        created_at=0,
        updated_at=0,
        enrolled_at=0,
    )
    db_session.add(e)
    await db_session.flush()


# ---------------------------------------------------------------------------
# scan_upcoming_events
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R85")
@pytest.mark.asyncio
async def test_upcoming_24h_reminder_fires_for_enrolled(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now + 23 * 60 * 60 * 1000  # 23h ahead — inside 24h window
    end = start + 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()
    assert sent >= 1

    rows = await _notifications_for(db_session, "amy", "event.upcoming_reminder")
    leads = sorted(r.payload["data"]["leadHours"] for r in rows)
    assert 24 in leads


@pytest.mark.requirement("notifications:R85")
@pytest.mark.asyncio
async def test_upcoming_1h_reminder_fires_separately(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now + 30 * 60 * 1000  # 30 minutes ahead — inside 1h window (and 24h)
    end = start + 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()
    rows = await _notifications_for(db_session, "amy", "event.upcoming_reminder")
    leads = sorted(r.payload["data"]["leadHours"] for r in rows)
    assert leads == [1, 24]  # both fire because event is inside both windows
    assert sent >= 2


@pytest.mark.requirement("notifications:R86")
@pytest.mark.asyncio
async def test_upcoming_idempotent_on_second_run(db_session: AsyncSession):
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now + 23 * 60 * 60 * 1000
    end = start + 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    _ = await scan_upcoming_events(db_session, now)
    await db_session.commit()
    second = await scan_upcoming_events(db_session, now)
    await db_session.commit()
    assert second == 0

    rows = await _notifications_for(db_session, "amy", "event.upcoming_reminder")
    assert len(rows) == 1


@pytest.mark.requirement("notifications:R85")
@pytest.mark.asyncio
async def test_upcoming_outside_window_silent(db_session: AsyncSession):
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now + 48 * 60 * 60 * 1000  # 48h ahead — outside 24h window
    end = start + 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    sent = await scan_upcoming_events(db_session, now)
    await db_session.commit()
    assert sent == 0


# ---------------------------------------------------------------------------
# scan_pending_marks
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R92")
@pytest.mark.asyncio
async def test_pending_mark_fires_for_ended_event_without_attendance(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "co")
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000  # ended 2h ago
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end, title="Past")
    event_id = event.id
    await _enroll(db_session, event_id, "amy")
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent >= 1

    for staff_name in ("admin", "co"):
        rows = await _notifications_for(
            db_session, staff_name, "attendance.pending_mark_reminder"
        )
        assert len(rows) == 1, f"{staff_name} should be notified once"
        data: Any = rows[0].payload["data"]
        assert data["eventId"] == event_id
        assert data["occurrenceTimeUtc"] == start


@pytest.mark.requirement("notifications:R93")
@pytest.mark.asyncio
async def test_pending_mark_silent_when_attendance_exists(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    db_session.add(
        AttendanceRecord(
            event_id=event.id,
            occurrence_time_utc=start,
            membername="amy",
            status=AttendanceStatus.present.value,
            recorded_at=now - 60_000,
        )
    )
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 0


@pytest.mark.requirement("notifications:R92")
@pytest.mark.asyncio
async def test_pending_mark_idempotent(db_session: AsyncSession):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    _ = await scan_pending_marks(db_session, now)
    await db_session.commit()
    second = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert second == 0
    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# scan_absence_streaks
# ---------------------------------------------------------------------------


async def _make_attendance(
    db_session: AsyncSession,
    *,
    statuses: list[AttendanceStatus],
    username: str,
    now: int,
) -> None:
    """Insert one Event + one AttendanceRecord per status, ordered oldest-first."""
    venue_id = await _ensure_venue(db_session)
    for i, status in enumerate(statuses):
        occ_time = now - (len(statuses) - i) * 24 * 60 * 60 * 1000
        event = await _make_event(
            db_session,
            start=occ_time,
            end=occ_time + 60_000,
            title=f"E{i}",
            venue_id=venue_id,
        )
        db_session.add(
            AttendanceRecord(
                event_id=event.id,
                occurrence_time_utc=occ_time,
                membername=username,
                status=status.value,
                recorded_at=now,
            )
        )
    await db_session.flush()


@pytest.mark.requirement("notifications:R94")
@pytest.mark.asyncio
async def test_absence_streak_warning_fires_at_threshold(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    await _make_attendance(
        db_session,
        statuses=[
            AttendanceStatus.absent,
            AttendanceStatus.absent,
            AttendanceStatus.absent,
        ],
        username="amy",
        now=now,
    )
    await db_session.commit()

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()
    assert sent == 1
    rows = await _notifications_for(
        db_session, "amy", "attendance.absence_streak_warning"
    )
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["streakLength"] >= 3


@pytest.mark.requirement("notifications:R94")
@pytest.mark.asyncio
async def test_absence_streak_silent_below_threshold(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    await _make_attendance(
        db_session,
        statuses=[AttendanceStatus.absent, AttendanceStatus.absent],
        username="amy",
        now=now,
    )
    await db_session.commit()

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()
    assert sent == 0


@pytest.mark.requirement("notifications:R95")
@pytest.mark.asyncio
async def test_absence_streak_idempotent(db_session: AsyncSession):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    await _make_attendance(
        db_session,
        statuses=[
            AttendanceStatus.absent,
            AttendanceStatus.absent,
            AttendanceStatus.absent,
        ],
        username="amy",
        now=now,
    )
    await db_session.commit()

    _ = await scan_absence_streaks(db_session, now)
    await db_session.commit()
    second = await scan_absence_streaks(db_session, now)
    await db_session.commit()
    assert second == 0


@pytest.mark.requirement("notifications:R96")
@pytest.mark.asyncio
async def test_absence_streak_resets_after_present(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    await _make_attendance(
        db_session,
        statuses=[
            AttendanceStatus.absent,
            AttendanceStatus.absent,
            AttendanceStatus.present,
            AttendanceStatus.absent,
            AttendanceStatus.absent,
        ],
        username="amy",
        now=now,
    )
    await db_session.commit()

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()
    # Max streak ended at 2 absences → below threshold
    assert sent == 0


# ---------------------------------------------------------------------------
# scan_pending_marks — cancelled occurrences (#336)
# ---------------------------------------------------------------------------


async def _cancel_occurrence(
    db_session: AsyncSession, event_id: int, occurrence_time: int
) -> None:
    """Cancel one occurrence the way the occurrence-cancel endpoint does."""
    db_session.add(
        OccurrenceOverride(
            event_id=event_id,
            occurrence_time=occurrence_time,
            status="cancelled",
        )
    )
    await db_session.flush()


@pytest.mark.requirement("notifications:R93")
@pytest.mark.asyncio
async def test_should_not_remind_when_occurrence_was_cancelled(
    db_session: AsyncSession,
):
    """#336: a cancelled session has no register to forget."""
    _ = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "co")
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end, title="Cancelled")
    await _enroll(db_session, event.id, "amy")
    await _cancel_occurrence(db_session, event.id, start)
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 0

    for staff_name in ("admin", "co"):
        rows = await _notifications_for(
            db_session, staff_name, "attendance.pending_mark_reminder"
        )
        assert rows == [], f"{staff_name} should not be reminded"


@pytest.mark.requirement("notifications:R93")
@pytest.mark.asyncio
async def test_should_not_remind_when_series_was_cancelled_before_occurrence(
    db_session: AsyncSession,
):
    """#336: event-level cancel — ``until_time`` at or before the occurrence."""
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    event.until_time = start
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 0

    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    assert rows == []


@pytest.mark.asyncio
async def test_should_remind_when_series_cancel_takes_effect_after_occurrence(
    db_session: AsyncSession,
):
    """#336: a cutoff later than the occurrence leaves that session live."""
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    event.until_time = start + 60 * 60 * 1000
    await _enroll(db_session, event.id, "amy")
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 1

    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    assert len(rows) == 1


@pytest.mark.requirement("notifications:R93")
@pytest.mark.asyncio
async def test_should_not_remind_when_occurrence_cancelled_and_no_attendance_rows(
    db_session: AsyncSession,
):
    """#336 guards #337: the skip must not depend on attendance rows existing.

    #337 will delete attendance records when an occurrence is cancelled. This
    asserts the cancellation check stands on its own, so clearing those rows
    cannot resurrect the reminder.
    """
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await _cancel_occurrence(db_session, event.id, start)
    await db_session.commit()

    attendance = await db_session.execute(
        select(AttendanceRecord).where(AttendanceRecord.event_id == event.id)
    )
    assert attendance.scalars().all() == [], "precondition: nothing marked"

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 0


@pytest.mark.asyncio
async def test_should_remind_when_occurrence_override_is_a_reschedule(
    db_session: AsyncSession,
):
    """#336: only a ``cancelled`` override silences the reminder.

    ``rescheduled`` is the other status the occurrence endpoints write. That
    session went ahead at a different time, so its register is still owed.
    """
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    db_session.add(
        OccurrenceOverride(
            event_id=event.id,
            occurrence_time=start,
            status="rescheduled",
            new_start_time=start + 60 * 60 * 1000,
            new_end_time=end + 60 * 60 * 1000,
        )
    )
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 1

    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_should_remind_when_a_different_occurrence_was_cancelled(
    db_session: AsyncSession,
):
    """#336: the cancellation must be matched to the occurrence being scanned."""
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    event = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event.id, "amy")
    await _cancel_occurrence(db_session, event.id, start + 7 * 24 * 60 * 60 * 1000)
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()
    assert sent == 1

    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    assert len(rows) == 1
    assert rows[0].payload["data"]["occurrenceTimeUtc"] == start


@pytest.mark.asyncio
async def test_should_remind_when_another_event_was_cancelled_at_the_same_time(
    db_session: AsyncSession,
):
    """#336: the cancellation must be matched to the event being scanned.

    Together with the different-occurrence case this pins both halves of the
    override's composite key, where a dropped filter would otherwise hide.
    """
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    start = now - 3 * 60 * 60 * 1000
    end = now - 2 * 60 * 60 * 1000
    other = await _make_event(db_session, start=start, end=end, title="Other")
    other_id = other.id
    await _cancel_occurrence(db_session, other_id, start)
    event = await _make_event(db_session, start=start, end=end, title="Scanned")
    event_id = event.id
    await _enroll(db_session, event_id, "amy")
    await db_session.commit()

    sent = await scan_pending_marks(db_session, now)
    await db_session.commit()

    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    reminded = [r.payload["data"]["eventId"] for r in rows]
    assert event_id in reminded, "the uncancelled event still needs its register"
    assert other_id not in reminded, "the cancelled one must stay silent"
    assert sent == 1
