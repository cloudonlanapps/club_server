"""The lifecycle predicates every other subsystem asks (#369).

``docs/event_lifecycle_requirements.md`` owns two questions and this module
is their only implementation:

- **L14** — an occurrence is cancelled if it carries a cancelled override or
  its slot falls at or after the event's cutoff. Nothing reads cancellation
  whole-event.
- **L12** — join-side flows are blocked when the event has no live
  occurrence at or after the moment of the request.

An occurrence moved by an occurrence-level reschedule keeps its slot as
its key; liveness and "past" read its override's new end (#472).

Attendance, credit, the pending-mark scan, the listing and enrollment all
call these rather than restating them, which is what keeps them agreeing
(programme R28, attendance R21a).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.event import Event
from ..db.models.occurrence_override import OccurrenceOverride
from .schedule import event_slots, schedule_for_slot

CANCELLED_OVERRIDE_STATUS = "cancelled"


def cutoff_of(event: Event) -> int | None:
    """The instant from which the event stops running, if it has one (L1, L2)."""
    return event.cutoff


def occurrence_is_cancelled(
    event: Event, occurrence_time_utc: int, override_status: str | None
) -> bool:
    """L14, given the occurrence's override status (``None`` when it has none)."""
    if override_status == CANCELLED_OVERRIDE_STATUS:
        return True
    cutoff = cutoff_of(event)
    return cutoff is not None and occurrence_time_utc >= cutoff


async def load_override_status(
    db: AsyncSession, event_id: int, occurrence_time_utc: int
) -> str | None:
    """The occurrence's override status, or ``None`` when it has no override."""
    result = await db.execute(
        select(OccurrenceOverride.status).where(
            OccurrenceOverride.event_id == event_id,
            OccurrenceOverride.occurrence_time == occurrence_time_utc,
        )
    )
    return result.scalar_one_or_none()


async def is_occurrence_cancelled(
    db: AsyncSession, event: Event, occurrence_time_utc: int
) -> bool:
    """L14, loading the override itself."""
    status = await load_override_status(db, event.id, occurrence_time_utc)
    return occurrence_is_cancelled(event, occurrence_time_utc, status)


async def _cancelled_slots(db: AsyncSession, event_id: int) -> set[int]:
    result = await db.execute(
        select(OccurrenceOverride.occurrence_time).where(
            OccurrenceOverride.event_id == event_id,
            OccurrenceOverride.status == CANCELLED_OVERRIDE_STATUS,
        )
    )
    return set(result.scalars().all())


async def _moved_ends(db: AsyncSession, event: Event) -> dict[int, int]:
    """Slot → new end, for each occurrence of ``event`` a reschedule moved (#472)."""
    result = await db.execute(
        select(
            OccurrenceOverride.occurrence_time, OccurrenceOverride.new_end_time
        ).where(
            OccurrenceOverride.event_id == event.id,
            OccurrenceOverride.new_end_time.is_not(None),
        )
    )
    return {
        slot: end
        for slot, end in result.tuples().all()
        if end is not None and schedule_for_slot(event, slot) is not None
    }


async def has_live_occurrence_at_or_after(
    db: AsyncSession, event: Event, now_ms: int
) -> bool:
    """L12: does any occurrence that is not cancelled end at or after ``now``?

    Existence, not enumeration (L13): an open-ended schedule answers yes at
    once; a bounded one is walked forward from ``now`` only as far as its
    first live slot. A postponed occurrence counts until its new end,
    however long ago its original slot was (#472).
    """
    current = event.current_schedule
    cutoff = current.effective_until
    if current.rrule and cutoff is None and "COUNT=" not in current.rrule.upper():
        return True
    cancelled = await _cancelled_slots(db, event.id)
    moved = await _moved_ends(db, event)
    for slot, end in moved.items():
        if slot in cancelled or (cutoff is not None and slot >= cutoff):
            continue
        if end >= now_ms:
            return True
    longest = max(s.end_time - s.start_time for s in event.schedules)
    for slot in event_slots(event, now_ms - longest, cutoff):
        if slot.time in cancelled:
            continue
        if moved.get(slot.time, slot.end) >= now_ms:
            return True
    return False


async def has_ended(db: AsyncSession, event: Event, now_ms: int) -> bool:
    """L7: no live occurrence remains at or after ``now``."""
    return not await has_live_occurrence_at_or_after(db, event, now_ms)


async def is_past(db: AsyncSession, event: Event, now_ms: int) -> bool:
    """Every occurrence of the series has finished, cancelled or not.

    The exit-side guard (enrollment R57, camp R61, R68): a member can leave
    and an admin can clean up a cancelled event, but a decision on an event
    whose last day has passed is a retrospective correction. Cancellation
    is deliberately ignored here — it closes joins (L12), not exits. A
    postponed occurrence finishes at its new end (#472).
    """
    current = event.current_schedule
    if current.rrule and "COUNT=" not in current.rrule.upper():
        return False
    moved = await _moved_ends(db, event)
    if any(end >= now_ms for end in moved.values()):
        return False
    longest = max(s.end_time - s.start_time for s in event.schedules)
    for slot in event_slots(event, now_ms - longest, None):
        if moved.get(slot.time, slot.end) >= now_ms:
            return False
    return True
