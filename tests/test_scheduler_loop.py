"""End-to-end tests for the scheduler loop itself.

Each test sets up a real scenario (event ended without attendance,
event starting inside the reminder window, etc.), starts
``scheduler_loop`` with a 1-second tick, waits for one tick, cancels
the task, and asserts the expected notification rows appear.

These exercise:
- the loop's cadence gating (hourly/daily gates start at 0 so the
  first tick runs every scan),
- the loop's session-factory plumbing,
- cancellation behaviour (the loop raises CancelledError cleanly).

They are deliberately separate from ``test_scheduler_scans.py``,
which exercises the scan functions directly with a fixed ``now``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
from club_server.db.models.enrollment import Enrollment, EnrollmentStatus
from club_server.db.models.event import Event
from club_server.db.models.notification import Notification
from club_server.db.models.venue import Venue
from club_server.services.scheduler import scheduler_loop

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
)


def future_ms(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


async def _ensure_venue(session: AsyncSession) -> int:
    venue = Venue(name="Loop Venue", created_at=0, updated_at=0)
    session.add(venue)
    await session.flush()
    return venue.id


async def _make_event(
    session: AsyncSession, *, start: int, end: int, title: str = "Practice"
) -> int:
    venue_id = await _ensure_venue(session)
    event = Event(
        title=title,
        type="programme",
        visibility="public",
        venue_id=venue_id,
        start_time=start,
        end_time=end,
        created_at=start - 1000,
        updated_at=start - 1000,
    )
    session.add(event)
    await session.flush()
    return event.id


async def _enroll(session: AsyncSession, event_id: int, username: str) -> None:
    session.add(
        Enrollment(
            event_id=event_id,
            membername=username,
            status=EnrollmentStatus.assigned.value,
            is_trial=0,
            created_at=0,
            updated_at=0,
            enrolled_at=0,
        )
    )
    await session.flush()


async def _notifications_for(
    session: AsyncSession, username: str, event_type: str
) -> list[Notification]:
    session.expire_all()
    result = await session.execute(
        select(Notification).where(
            Notification.username == username,
            Notification.type == event_type,
        )
    )
    return list(result.scalars().all())


async def _run_loop_once(session_factory, tick_seconds: float = 1.0) -> None:
    """Start the loop, give it ~one tick, cancel, swallow CancelledError."""
    task = asyncio.create_task(
        scheduler_loop(session_factory, tick_seconds=tick_seconds)
    )
    await asyncio.sleep(tick_seconds * 1.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest_asyncio.fixture(scope="function")
async def loop_session_factory(test_engine):
    """Build a session factory bound to the per-test test_engine.

    Cannot reuse the per-test ``db_session`` fixture because the loop
    opens its own fresh sessions on each tick — we need a factory, not
    a single session. Bind to the same engine so the loop sees the
    rows the test set up.
    """
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.requirement("notifications:R85")
@pytest.mark.asyncio
async def test_loop_emits_upcoming_event_reminder(
    db_session: AsyncSession, loop_session_factory
):
    _ = await create_member_user(db_session, "amy")
    start = future_ms(23)  # inside 24h window
    end = future_ms(24)
    event_id = await _make_event(db_session, start=start, end=end)
    await _enroll(db_session, event_id, "amy")
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    rows = await _notifications_for(db_session, "amy", "event.upcoming_reminder")
    leads = sorted(r.payload["data"]["leadHours"] for r in rows)
    assert 24 in leads
    data: Any = rows[0].payload["data"]
    assert data["eventId"] == event_id


@pytest.mark.requirement("notifications:R92")
@pytest.mark.asyncio
async def test_loop_emits_pending_mark_reminder(
    db_session: AsyncSession, loop_session_factory
):
    _ = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "co")
    _ = await create_member_user(db_session, "amy")
    start = future_ms(-3)  # ended 2h ago
    end = future_ms(-2)
    event_id = await _make_event(db_session, start=start, end=end, title="Past")
    await _enroll(db_session, event_id, "amy")
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    for staff_name in ("admin", "co"):
        rows = await _notifications_for(
            db_session, staff_name, "attendance.pending_mark_reminder"
        )
        assert len(rows) == 1, f"{staff_name} should be notified once"
        assert rows[0].payload["data"]["eventId"] == event_id


@pytest.mark.requirement("notifications:R94")
@pytest.mark.asyncio
async def test_loop_emits_absence_streak_warning(
    db_session: AsyncSession, loop_session_factory
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    now = future_ms(0)
    venue_id = await _ensure_venue(db_session)
    for i in range(3):
        occ = now - (3 - i) * 24 * 60 * 60 * 1000
        event = Event(
            title=f"E{i}",
            type="programme",
            visibility="public",
            venue_id=venue_id,
            start_time=occ,
            end_time=occ + 60_000,
            created_at=0,
            updated_at=0,
        )
        db_session.add(event)
        await db_session.flush()
        db_session.add(
            AttendanceRecord(
                event_id=event.id,
                occurrence_time_utc=occ,
                membername="amy",
                status=AttendanceStatus.absent.value,
                recorded_at=now,
            )
        )
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    rows = await _notifications_for(
        db_session, "amy", "attendance.absence_streak_warning"
    )
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["streakLength"] >= 3


# ---------------------------------------------------------------------------
# #476: one failing scan does not starve the others
# ---------------------------------------------------------------------------


async def _absent_three_times(session: AsyncSession, username: str, now: int) -> None:
    venue_id = await _ensure_venue(session)
    for i in range(3):
        occ = now - (3 - i) * 24 * 60 * 60 * 1000
        event = Event(
            title=f"E{i}",
            type="programme",
            visibility="public",
            venue_id=venue_id,
            start_time=occ,
            end_time=occ + 60_000,
            created_at=0,
            updated_at=0,
        )
        session.add(event)
        await session.flush()
        session.add(
            AttendanceRecord(
                event_id=event.id,
                occurrence_time_utc=occ,
                membername=username,
                status=AttendanceStatus.absent.value,
                recorded_at=now,
            )
        )
    await session.flush()


async def _boom(*_args: Any, **_kwargs: Any) -> int:
    raise RuntimeError("scan failed")


@pytest.mark.asyncio
async def test_should_run_daily_scans_when_credit_sweep_fails(
    db_session: AsyncSession, loop_session_factory, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "club_server.services.scheduler.sweep_credit_settlements", _boom
    )
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await _absent_three_times(db_session, "amy", future_ms(0))
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    rows = await _notifications_for(
        db_session, "amy", "attendance.absence_streak_warning"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_should_run_hourly_scans_when_upcoming_scan_fails(
    db_session: AsyncSession, loop_session_factory, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("club_server.services.scheduler.scan_upcoming_events", _boom)
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    event_id = await _make_event(
        db_session, start=future_ms(-3), end=future_ms(-2), title="Past"
    )
    await _enroll(db_session, event_id, "amy")
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    rows = await _notifications_for(
        db_session, "admin", "attendance.pending_mark_reminder"
    )
    assert [r.payload["data"]["eventId"] for r in rows] == [event_id]


@pytest.mark.requirement("groups:R84")
@pytest.mark.asyncio
async def test_loop_reports_a_semi_auto_member_who_no_longer_matches(
    db_session: AsyncSession, loop_session_factory
):
    """The daily cadence runs the group-eligibility scan (#17)."""
    from club_server.db.models.group import Group, GroupMember

    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy", gender="male")
    group = Group(name="Girls", kind="semi_auto", gender="female", created_at=0)
    db_session.add(group)
    await db_session.flush()
    group_id = group.id
    db_session.add(GroupMember(group_id=group_id, membername="amy"))
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    rows = await _notifications_for(db_session, "admin", "group.member_ineligible")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data == {"groupId": group_id, "groupName": "Girls", "membername": "amy"}


@pytest.mark.requirement("eligibility:R21")
@pytest.mark.asyncio
async def test_loop_reports_a_programme_member_who_no_longer_matches(
    db_session: AsyncSession, loop_session_factory
):
    """The daily cadence runs the enrolment-eligibility scan (#19)."""
    from sqlalchemy import update

    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy", gender="male")
    start = future_ms(48)
    event_id = await _make_event(
        db_session, start=start, end=start + 60 * 60 * 1000, title="Girls skate"
    )
    _ = await db_session.execute(
        update(Event).where(Event.id == event_id).values(gender="female")
    )
    await _enroll(db_session, event_id, "amy")
    await db_session.commit()

    await _run_loop_once(loop_session_factory)

    rows = await _notifications_for(db_session, "admin", "enrollment.member_ineligible")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data == {
        "eventId": event_id,
        "eventTitle": "Girls skate",
        "membername": "amy",
    }
