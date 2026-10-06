"""Periodic scans that emit time-triggered notifications.

Three scans, run on different cadences by ``scheduler_loop`` (started
from the FastAPI lifespan in ``main.py``):

- ``scan_upcoming_events``     — every 5 minutes
- ``scan_pending_marks``       — every hour
- ``sweep_credit_settlements`` — every hour (``credit_sweep.py``)
- ``scan_absence_streaks``     — every 24 hours (``absence_streak.py``)
- ``scan_group_eligibility``   — every 24 hours (``group_eligibility_scan.py``)

All scans are idempotent: if a notification has already been emitted
for the relevant ``(user, event, occurrence, lead)`` key, the scan
skips it. This lets the loop run as often as it likes without
producing duplicate rows on restart, missed ticks, or overlapping
runs.

The reminder scans expand each event's occurrences from its schedules for
their window (#475), and skip an occurrence ``lifecycle.py`` says is
cancelled.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord
from ..db.models.broadcast import Broadcast
from ..db.models.enrollment import Enrollment
from ..db.models.event import Event
from ..db.models.event_schedule import EventSchedule
from ..db.models.notification import Notification
from ..db.models.user import User, UserStatus
from ..schemas.common import UserRoles
from ..utils import ceil_to_utc_day
from .absence_streak import scan_absence_streaks
from .group_eligibility_scan import scan_group_eligibility
from .credit_sweep import sweep_credit_settlements
from .inquiry import purge_expired_inquiries
from .event import NOTIFIABLE_ENROLLMENT_STATUSES
from .lifecycle import is_occurrence_cancelled
from .notification import NotificationEvent, NotificationService
from .schedule import Slot, duration_of, event_slots
from .system_preferences import SystemPreferenceService

logger = logging.getLogger(__name__)


UPCOMING_LEAD_HOURS: tuple[int, ...] = (24, 1)
PENDING_MARK_WINDOW_DAYS: int = 7
"""How far back to look for occurrences whose attendance is still missing."""

_MS_PER_HOUR = 60 * 60 * 1000
_MS_PER_DAY = 24 * _MS_PER_HOUR


async def _list_enrolled_members(session: AsyncSession, event_id: int) -> list[str]:
    result = await session.execute(
        select(Enrollment.membername).where(
            Enrollment.event_id == event_id,
            Enrollment.status.in_(NOTIFIABLE_ENROLLMENT_STATUSES),
        )
    )
    return [row for (row,) in result.all()]


async def _list_staff_usernames(session: AsyncSession) -> list[str]:
    """Active admins and coaches (recipients for pending-mark reminders)."""
    result = await session.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.status == UserStatus.active.value,
        )
    )
    staff: list[str] = []
    for u in result.scalars().all():
        if u.is_super_admin:
            staff.append(u.username)
            continue
        if not u.roles:
            continue
        parsed = UserRoles.model_validate_json(u.roles)
        if "admin" in parsed.roles or "coach" in parsed.roles:
            staff.append(u.username)
    return staff


async def _existing_payloads(
    session: AsyncSession, username: str, notification_type: str
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Notification.payload).where(
            Notification.username == username,
            Notification.type == notification_type,
        )
    )
    return [row for (row,) in result.all()]


async def _events_with_slots_before(session: AsyncSession, before: int) -> list[Event]:
    """Live events with a schedule that can produce a slot before ``before``.

    A schedule's slots all fall at or after its own ``start_time``, so an
    event whose every schedule starts later has nothing in the window. This
    only narrows the candidates; ``event_slots`` decides per occurrence.
    """
    has_schedule = (
        select(EventSchedule.id)
        .where(EventSchedule.event_id == Event.id, EventSchedule.start_time < before)
        .exists()
    )
    result = await session.execute(
        select(Event).where(Event.deleted_at.is_(None), has_schedule)
    )
    return list(result.scalars().all())


async def _live_slots(
    session: AsyncSession, event: Event, from_ms: int, to_ms: int
) -> list[Slot]:
    """The event's occurrences in ``[from_ms, to_ms)`` that are not cancelled.

    Expanded from the schedules, so every occurrence of a programme or camp
    counts, not only the first (#475). Cancellation — by override or by the
    cutoff — is ``lifecycle.py``'s question (L14).
    """
    live: list[Slot] = []
    for slot in event_slots(event, from_ms, to_ms):
        if not await is_occurrence_cancelled(session, event, slot.time):
            live.append(slot)
    return live


async def scan_upcoming_events(session: AsyncSession, now: int) -> int:
    """Emit ``event.upcoming_reminder`` to enrolled users for occurrences
    starting within each configured lead time.

    Every live occurrence in ``(now, now + lead]`` is reminded once per
    lead; cancelled occurrences and those at or after the event's cutoff are
    skipped. Returns the number of notifications emitted on this pass.
    """
    notifier = NotificationService(session)
    sent = 0

    for lead_hours in UPCOMING_LEAD_HOURS:
        window_end = now + lead_hours * _MS_PER_HOUR

        for event in await _events_with_slots_before(session, window_end + 1):
            slots = await _live_slots(session, event, now + 1, window_end + 1)
            if not slots:
                continue
            recipients = await _list_enrolled_members(session, event.id)
            if not recipients:
                continue

            for slot in slots:
                for username in recipients:
                    payloads = await _existing_payloads(
                        session, username, "event.upcoming_reminder"
                    )
                    already = any(
                        p.get("data", {}).get("eventId") == event.id
                        and p.get("data", {}).get("occurrenceTimeUtc") == slot.time
                        and p.get("data", {}).get("leadHours") == lead_hours
                        for p in payloads
                    )
                    if already:
                        continue

                    emitted = await notifier.notify_for_event(
                        NotificationEvent(
                            type="event.upcoming_reminder",
                            recipients=[username],
                            data={
                                "eventId": event.id,
                                "eventTitle": event.title,
                                "occurrenceTimeUtc": slot.time,
                                "leadHours": lead_hours,
                            },
                        )
                    )
                    sent += len(emitted)

    return sent


async def scan_pending_marks(session: AsyncSession, now: int) -> int:
    """Emit ``attendance.pending_mark_reminder`` to active staff when an
    occurrence has ended but no attendance row exists for it.

    Considers every occurrence that ended in the last
    ``PENDING_MARK_WINDOW_DAYS``, expanded from the schedules (#475).
    Returns the number of notifications emitted on this pass.
    """
    notifier = NotificationService(session)
    sent = 0
    window_start = now - PENDING_MARK_WINDOW_DAYS * _MS_PER_DAY

    events = await _events_with_slots_before(session, now)
    if not events:
        return 0

    staff = await _list_staff_usernames(session)
    if not staff:
        return 0

    for event in events:
        longest = max(duration_of(s) for s in event.schedules)
        # A cancelled session has no register to forget, so it must not
        # produce a pending-mark reminder (#336, attendance R21a).
        slots = [
            slot
            for slot in await _live_slots(session, event, window_start - longest, now)
            if window_start <= slot.end < now
        ]
        for slot in slots:
            attendance = await session.execute(
                select(AttendanceRecord.id)
                .where(
                    AttendanceRecord.event_id == event.id,
                    AttendanceRecord.occurrence_time_utc == slot.time,
                )
                .limit(1)
            )
            if attendance.scalar_one_or_none() is not None:
                continue

            for username in staff:
                payloads = await _existing_payloads(
                    session, username, "attendance.pending_mark_reminder"
                )
                already = any(
                    p.get("data", {}).get("eventId") == event.id
                    and p.get("data", {}).get("occurrenceTimeUtc") == slot.time
                    for p in payloads
                )
                if already:
                    continue

                emitted = await notifier.notify_for_event(
                    NotificationEvent(
                        type="attendance.pending_mark_reminder",
                        recipients=[username],
                        data={
                            "eventId": event.id,
                            "eventTitle": event.title,
                            "occurrenceTimeUtc": slot.time,
                            "occurrenceEndTimeUtc": slot.end,
                        },
                    )
                )
                sent += len(emitted)

    return sent


async def sweep_expired_notifications(session: AsyncSession, now: int) -> int:
    """Delete non-actionable notifications past their effective expiry.

    Effective expiry per row:
      - Broadcast-linked with the broadcast's ``expires_at`` set: that
        value (already ceiled to midnight UTC on write).
      - Otherwise: ``ceil_to_utc_day(created_at + retention_days * MS_PER_DAY)``
        where ``retention_days`` comes from the
        ``notification_info_retention_days`` system preference.

    Rows with a pending unresolved action (``pending_action_type IS NOT NULL``)
    are exempt — they are cleared on action completion, not by this sweep.

    Returns the number of rows deleted.
    """
    pref_service = SystemPreferenceService(session)
    retention_days = int(
        await pref_service.get_value("notification_info_retention_days")
    )
    retention_ms = retention_days * _MS_PER_DAY

    rows = (
        await session.execute(
            select(
                Notification.id,
                Notification.created_at,
                Notification.broadcast_id,
                Broadcast.expires_at,
            )
            .outerjoin(Broadcast, Broadcast.id == Notification.broadcast_id)
            .where(Notification.pending_action_type.is_(None))
        )
    ).all()

    ids_to_delete: list[int] = []
    for nid, created_at, broadcast_id, broadcast_expires_at in rows:
        if broadcast_id is not None and broadcast_expires_at is not None:
            effective_expiry = broadcast_expires_at
        else:
            effective_expiry = ceil_to_utc_day(created_at + retention_ms)
        if now >= effective_expiry:
            ids_to_delete.append(nid)

    if not ids_to_delete:
        return 0

    _ = await session.execute(
        sa_delete(Notification).where(Notification.id.in_(ids_to_delete))
    )
    return len(ids_to_delete)


# ---------------------------------------------------------------------------
# Loop integration
# ---------------------------------------------------------------------------


SCHEDULER_TICK_SECONDS: int = 300
"""How often the loop wakes up. Upcoming-event scan runs every tick;
pending-mark and absence-streak scans run on their own internal cadence
(hourly / daily) gated by timestamps tracked in the loop."""


async def _run_scan(session: AsyncSession, name: str, scan, now: int) -> None:
    """Run one scan in its own transaction (#476).

    A failing scan is logged and rolled back without touching the others,
    so one bad row cannot starve every other scan of the tick.
    """
    try:
        _ = await scan(session, now)
        await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        await session.rollback()
        logger.exception("scheduler scan %s failed", name)


async def scheduler_loop(
    session_factory,
    *,
    tick_seconds: int = SCHEDULER_TICK_SECONDS,
) -> None:
    """Long-running task. Stop by cancelling the task.

    Each scan runs in its own try (``_run_scan``), and the hourly and daily
    cadences advance whether or not a scan in them failed: a failing scan
    is retried at its next slot rather than holding every other scan back.
    """
    from ..utils import now_utc_ms

    last_hour = 0
    last_day = 0
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            now = now_utc_ms()
            scans = [("upcoming", scan_upcoming_events)]
            if now - last_hour >= _MS_PER_HOUR:
                scans += [
                    ("pending_marks", scan_pending_marks),
                    ("credit_settlements", sweep_credit_settlements),
                ]
                last_hour = now
            if now - last_day >= _MS_PER_DAY:
                scans += [
                    ("absence_streaks", scan_absence_streaks),
                    ("group_eligibility", scan_group_eligibility),
                    ("expired_notifications", sweep_expired_notifications),
                    ("expired_inquiries", purge_expired_inquiries),
                ]
                last_day = now
            async with session_factory() as session:
                for name, scan in scans:
                    await _run_scan(session, name, scan, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler tick failed")
