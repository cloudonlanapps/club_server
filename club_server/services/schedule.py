"""Occurrence generation over an event's schedules (#370, #388).

An occurrence comes from exactly one schedule: its rule expanded from its
``start_time`` and clipped to the period the schedule owns — from its
``effective_from`` to the next schedule's ``effective_from``. An event's
occurrences are the union over its schedules; because the periods are
contiguous and non-overlapping, nothing is produced twice and nothing is
lost at a boundary (``docs/event_schedule_model.md``).

The cutoff is deliberately not applied here: a slot at or after the cutoff
is still a slot of the series — a cancelled one (lifecycle L14) — and the
listing shows it as such. ``services/lifecycle.py`` owns that question.

Every function takes a window, so a programme of any age is expanded forward
from the moment that matters rather than from its first day (programme R18),
and slot membership is decided by matching an instant against the rule, not
by enumerating the series (R4).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .rrule import generate_occurrences

if TYPE_CHECKING:
    from ..db.models.event import Event
    from ..db.models.event_schedule import EventSchedule

GENERATION_CAP = 100_000
"""Not a scheduling bound: a guard against an unbounded expansion with no
upper window, which no caller should make."""


@dataclass(frozen=True)
class Slot:
    """One occurrence: its slot time and the schedule that produced it."""

    time: int
    schedule: "EventSchedule"

    @property
    def end(self) -> int:
        """The occurrence's template end: slot plus the schedule's duration."""
        return self.time + duration_of(self.schedule)


def duration_of(schedule: "EventSchedule") -> int:
    """The length of one occurrence, in milliseconds."""
    return schedule.end_time - schedule.start_time


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _expand(schedule: "EventSchedule", from_ms: int, to_ms: int | None) -> list[int]:
    starts = generate_occurrences(
        schedule.rrule,
        _dt(schedule.start_time),
        _dt(schedule.end_time),
        None,
        _dt(from_ms),
        _dt(to_ms) if to_ms is not None else None,
        max_count=GENERATION_CAP,
    )
    return [int(s.timestamp() * 1000) for s in starts]


def _whole_second(ms: int) -> int:
    """Rule expansion is second-granular: a slot never carries milliseconds,
    so a period boundary is compared at the same precision."""
    return (ms // 1000) * 1000


def owned_until(event: "Event", schedule: "EventSchedule") -> int | None:
    """Where ``schedule`` stops owning slots: the next schedule's start, if any."""
    later = [
        s.effective_from
        for s in event.schedules
        if s.effective_from > schedule.effective_from
    ]
    return min(later) if later else None


def schedule_slots(
    event: "Event", schedule: "EventSchedule", from_ms: int, to_ms: int | None
) -> list[int]:
    """Slot times ``schedule`` owns inside ``[from_ms, to_ms)``."""
    lower = max(from_ms, _whole_second(schedule.effective_from))
    bounds = [b for b in (to_ms, owned_until(event, schedule)) if b is not None]
    upper = min(bounds) if bounds else None
    if upper is not None and lower >= upper:
        return []
    return _expand(schedule, lower, upper)


def event_slots(event: "Event", from_ms: int, to_ms: int | None) -> list[Slot]:
    """Every slot of ``event`` inside ``[from_ms, to_ms)``, in time order."""
    slots: list[Slot] = []
    for schedule in event.schedules:
        slots.extend(
            Slot(t, schedule) for t in schedule_slots(event, schedule, from_ms, to_ms)
        )
    slots.sort(key=lambda s: s.time)
    return slots


def schedule_for_slot(event: "Event", slot_ms: int) -> "EventSchedule | None":
    """The schedule that produces ``slot_ms``, or ``None`` if none does."""
    for schedule in event.schedules:
        if slot_ms in schedule_slots(event, schedule, slot_ms, slot_ms + 1):
            return schedule
    return None


def is_slot_of_current_schedule(event: "Event", slot_ms: int) -> bool:
    """Whether ``slot_ms`` is an occurrence start of the current schedule's rule.

    The cutoff a terminate, extend or split names must be one of these
    (programme R2, R4). The current schedule's own cutoff is ignored, so a
    bounded programme can be extended to a later slot of the same rule.
    """
    schedule = event.current_schedule
    if slot_ms < _whole_second(schedule.effective_from):
        return False
    return slot_ms in _expand(schedule, slot_ms, slot_ms + 1)


def first_slot(event: "Event") -> int | None:
    """The event's first occurrence, if its rule produces one."""
    slots = event_slots(event, event.first_schedule.effective_from, None)
    return slots[0].time if slots else None


def last_slot(event: "Event") -> int | None:
    """The event's last occurrence, for a series bounded by rule or cutoff.

    ``None`` for an open-ended series, which has no last occurrence.
    """
    current = event.current_schedule
    if (
        current.rrule
        and current.effective_until is None
        and "COUNT=" not in current.rrule.upper()
    ):
        return None
    slots = event_slots(
        event, event.first_schedule.effective_from, current.effective_until
    )
    return slots[-1].time if slots else None
