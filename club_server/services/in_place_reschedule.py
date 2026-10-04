"""Rewriting the one schedule a camp or a one-off has (#230, #232, #248).

The schedule fields generate the slot times that overrides and attendance
records are keyed by, so rewriting them could orphan those rows. Two guards
make that impossible (camp R2c): the series must not have started — no
occurrence in the past and no attendance record — and overrides must be
absent unless the caller asks for them to be reset. A reset clears each row
rather than deleting it, so no occurrence's version goes backwards
(lifecycle L23b).

Conflicts are reported, never blocked, on this path (one-off R21): a camp
or a one-off is finite and the club may decide to run it over something.
"""

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ..db.models.attendance import AttendanceRecord
from ..db.models.event import Event
from ..db.models.occurrence_override import OccurrenceOverride
from ..exceptions import (
    EventAlreadyStartedException,
    OccurrenceOverridesPresentException,
)
from ..schemas.common import ChangeLog
from ..schemas.event import Session
from ..utils import now_utc_ms
from .conflict_gates import (
    ConflictGate,
    GatePolicy,
    check_conflicts,
    target_for_event,
)
from .occurrence_version import SCHEDULED_OVERRIDE_STATUS, clear_override
from .schedule import first_slot
from .schedule_writes import (
    decode_sessions,
    encode_sessions,
    validate_horizon,
    validate_timetable,
    validate_venue,
    validate_window,
)

if TYPE_CHECKING:
    from .event import EventService


async def reschedule_in_place(
    service: "EventService",
    event: Event,
    *,
    start_time: int | None,
    end_time: int | None,
    rrule: str | None,
    venue_id: int | None,
    sessions: list[Session] | None,
    update_sessions: bool,
    reset_overrides: bool,
    actor: str | None = None,
) -> Event:
    """Apply the new schedule fields to the event's single schedule."""
    db = service.db
    now = now_utc_ms()
    schedule = event.current_schedule

    # Guard 1: the series must not have started.
    first = first_slot(event)
    attendance_count = await db.scalar(
        select(func.count())
        .select_from(AttendanceRecord)
        .where(AttendanceRecord.event_id == event.id)
    )
    if (first is not None and now >= first) or (attendance_count or 0) > 0:
        raise EventAlreadyStartedException(event.id)

    # Guard 2: occurrence overrides must be absent or explicitly reset.
    overrides = list(
        (
            await db.execute(
                select(OccurrenceOverride).where(
                    OccurrenceOverride.event_id == event.id,
                    OccurrenceOverride.status != SCHEDULED_OVERRIDE_STATUS,
                )
            )
        )
        .scalars()
        .all()
    )
    if overrides:
        if not reset_overrides:
            raise OccurrenceOverridesPresentException(
                sorted(o.occurrence_time for o in overrides)
            )
        for override in overrides:
            clear_override(override, actor)

    old_start, old_end = schedule.start_time, schedule.end_time
    changes = ChangeLog()
    if venue_id is not None and venue_id != schedule.venue_id:
        await validate_venue(db, venue_id)
        changes.add("venue_id", schedule.venue_id, venue_id)
        schedule.venue_id = venue_id
    if rrule is not None and rrule != schedule.rrule:
        changes.add("rrule", schedule.rrule, rrule)
        schedule.rrule = rrule
    if start_time is not None and start_time != schedule.start_time:
        changes.add("start_time", schedule.start_time, start_time)
        schedule.start_time = start_time
        schedule.effective_from = start_time
    if end_time is not None:
        if end_time != schedule.end_time:
            changes.add("end_time", schedule.end_time, end_time)
            schedule.end_time = end_time
    elif start_time is not None:
        # Only the start moved: shift the end by the same delta so each
        # occurrence keeps its duration.
        shifted_end = old_end + (schedule.start_time - old_start)
        if shifted_end != schedule.end_time:
            changes.add("end_time", schedule.end_time, shifted_end)
            schedule.end_time = shifted_end

    validate_window(schedule.start_time, schedule.end_time)
    validate_horizon(event.type, schedule.start_time, now)

    # Resolve the timetable to validate against the new window: a supplied
    # one replaces the stored timetable (``None`` clears it); otherwise the
    # existing one is re-validated so a window that no longer sums is rejected.
    if update_sessions:
        schedule.sessions = encode_sessions(sessions)
    validate_timetable(
        decode_sessions(schedule.sessions), schedule.start_time, schedule.end_time
    )

    schedule.updated_at = now
    event.touch(actor, now)
    await db.flush()

    report = await check_conflicts(
        db,
        target_for_event(event),
        {
            ConflictGate.venue: GatePolicy.advise,
            ConflictGate.organizer: GatePolicy.advise,
            ConflictGate.coach: GatePolicy.advise,
        },
    )
    await service.notify_conflicts(event, report.findings)
    await service.notify_schedule_change(event, changes)
    return event
