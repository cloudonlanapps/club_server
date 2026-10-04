"""Conflict detection as named gates (#382, #288, #17 — programme R31b, R31c).

Four gates, each answering one question about one resource — venue,
organizer, coach, member. A gate computes findings; the caller declares
which gates run and whether each blocks or advises. Overlap is defined once
(camp R32) and every gate uses it.

**Comparison.** Two schedules are compared over the intersection of the
periods they run for. A camp or one-off is finite, bounded by its rule and
the scheduling horizon, so that intersection is finite and both sides are
expanded within it. Two weekly programmes repeat with a period of seven
days, so one such period of their overlap decides the question exactly and
an open-ended programme needs no horizon (R30a); a window that crosses
midnight is compared as the two day-parts it occupies (R30a1). A candidate's
occurrences honour its overrides and its cutoff, so a cancelled occurrence
never clashes and a terminated programme counts only up to its cutoff
(camp R34, enrollment R29b).

**Policy.** ``GatePolicy.block`` blocks only a programme-against-programme
clash; every other pairing is reported whatever the caller asked, because a
short event may not prevent or displace a programme and a programme may not
prevent one (R30, R30c, one-off R20). ``GatePolicy.advise`` never raises.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.event import Event
from ..db.models.event_schedule import EventSchedule
from ..db.models.occurrence_override import OccurrenceOverride
from ..exceptions import EventConflictException
from ..schemas.venue import ConflictReport, VenueConflictItem
from .event_types import blocks_on_conflict
from .schedule import duration_of, event_slots, last_slot

OccurrencePair = tuple[int, int, int, int]
"""(target_start_ms, target_end_ms, other_start_ms, other_end_ms)."""

RULE_COMPARISON_WINDOW_MS = 14 * 24 * 60 * 60 * 1000
"""Two seven-day periods: enough for a weekly pattern's clash set to show
itself, including a window that spills across midnight into the next day."""

ENROLLED_STATUSES = {
    EnrollmentStatus.accepted.value,
    EnrollmentStatus.assigned.value,
    EnrollmentStatus.assigned_trial.value,
    EnrollmentStatus.withdraw_requested.value,
}


class ConflictGate(StrEnum):
    """The resource a gate asks about."""

    venue = "venue"
    organizer = "organizer"
    coach = "coach"
    member = "member"


class GatePolicy(StrEnum):
    """What a finding does: block the caller's write, or come back with it."""

    block = "block"
    advise = "advise"


@dataclass(frozen=True)
class ScheduleTarget:
    """The schedule being checked, in the shape every event type shares.

    ``effective_until`` bounds the period; ``None`` is open-ended. A single
    moved occurrence is a target with no rule whose period is its window.
    """

    event_type: str
    venue_id: int
    start_time: int
    end_time: int
    rrule: str | None
    effective_from: int
    effective_until: int | None = None
    organizer_name: str | None = None
    coach_names: tuple[str, ...] = ()
    membername: str | None = None
    exclude_event_id: int | None = None


@dataclass(frozen=True)
class Finding:
    """One clashing event, with the occurrence pairs that overlap."""

    gate: ConflictGate
    event_id: int
    event_title: str
    event_type: str
    occurrences: list[OccurrencePair] = field(default_factory=list)


@dataclass(frozen=True)
class GateReport:
    """Every advisory finding the requested gates produced."""

    findings: list[Finding] = field(default_factory=list)

    def for_gate(self, gate: ConflictGate) -> list[Finding]:
        """The findings one gate produced."""
        return [f for f in self.findings if f.gate is gate]


def overlaps(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    """Camp R32: two half-open windows overlap iff ``start_a < end_b and end_a > start_b``."""
    return start_a < end_b and end_a > start_b


def pairwise_overlap(
    target: list[tuple[int, int]], other: list[tuple[int, int]]
) -> list[OccurrencePair]:
    """Every (target, other) pair of windows that overlap."""
    return [
        (ts, te, os_, oe)
        for ts, te in target
        for os_, oe in other
        if overlaps(ts, te, os_, oe)
    ]


def _is_open_weekly(rrule: str | None, until: int | None) -> bool:
    return bool(rrule) and until is None and "COUNT=" not in (rrule or "").upper()


def _target_windows(
    target: ScheduleTarget, lo: int, hi: int | None
) -> list[tuple[int, int]]:
    from .rrule import generate_occurrences
    from datetime import datetime, timezone

    def dt(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    starts = generate_occurrences(
        target.rrule,
        dt(target.start_time),
        dt(target.end_time),
        None,
        dt(lo),
        dt(hi) if hi is not None else None,
        max_count=100_000,
    )
    duration = target.end_time - target.start_time
    return [
        (int(s.timestamp() * 1000), int(s.timestamp() * 1000) + duration)
        for s in starts
    ]


def _candidate_windows(
    candidate: Event,
    overrides: dict[int, OccurrenceOverride],
    lo: int,
    hi: int | None,
    schedules: set[int] | None = None,
) -> list[tuple[int, int]]:
    """The candidate's live occurrence windows in ``[lo, hi)``.

    ``schedules`` narrows the slots to those produced by the given schedule
    ids — the ones holding the resource a gate asks about.
    """
    cutoff = candidate.cutoff
    upper = hi if cutoff is None else (cutoff if hi is None else min(hi, cutoff))
    windows: list[tuple[int, int]] = []
    for slot in event_slots(candidate, lo, upper):
        if schedules is not None and slot.schedule.id not in schedules:
            continue
        start, end = slot.time, slot.end
        override = overrides.get(slot.time)
        if override is not None:
            if override.status == "cancelled":
                continue
            if override.new_start_time is not None:
                start = override.new_start_time
                end = override.new_end_time or start + duration_of(slot.schedule)
            elif override.new_end_time is not None:
                end = override.new_end_time
        windows.append((start, end))
    return windows


def _is_weekly_pattern(rrule: str | None, until: int | None) -> bool:
    return _is_open_weekly(rrule, until)


def _comparison_range(
    target: ScheduleTarget, candidate: Event
) -> tuple[int, int | None] | None:
    """The period over which the two are expanded, or ``None`` if disjoint.

    The union of both periods rather than their intersection, so that an
    occurrence rescheduled away from its slot is still seen. Every bounded
    side contributes its end; two open weekly patterns are decided by one
    repeat period after the later of them starts (R30a).
    """
    starts = [target.effective_from, candidate.first_schedule.effective_from]
    ends: list[int] = []
    if target.effective_until is not None:
        ends.append(target.effective_until)
    if candidate.cutoff is not None:
        ends.append(candidate.cutoff)
    if not _is_open_weekly(target.rrule, target.effective_until):
        target_windows = _target_windows(target, target.effective_from, None)
        if not target_windows:
            return None
        ends.append(target_windows[-1][1])
    candidate_last = last_slot(candidate)
    if candidate_last is not None:
        ends.append(candidate_last + duration_of(candidate.current_schedule))
    lo = min(starts)
    hi = max(ends) if ends else max(starts) + RULE_COMPARISON_WINDOW_MS
    current = candidate.current_schedule
    if _is_weekly_pattern(target.rrule, target.effective_until) and _is_weekly_pattern(
        current.rrule, current.effective_until
    ):
        hi = min(hi, max(starts) + RULE_COMPARISON_WINDOW_MS)
    if lo >= hi:
        return None
    return lo, hi


def _pairs(
    target: ScheduleTarget,
    candidate: Event,
    overrides: dict[int, OccurrenceOverride],
    schedules: set[int] | None,
) -> list[OccurrencePair]:
    period = _comparison_range(target, candidate)
    if period is None:
        return []
    lo, hi = period
    # Look one occurrence back so a window already under way at ``lo`` counts.
    reach = max(
        target.end_time - target.start_time,
        max(duration_of(s) for s in candidate.schedules),
    )
    return pairwise_overlap(
        _target_windows(target, lo - reach, hi),
        _candidate_windows(candidate, overrides, lo - reach, hi, schedules),
    )


def _resource_matches(
    gate: ConflictGate, target: ScheduleTarget, schedule: EventSchedule
) -> bool:
    if gate is ConflictGate.venue:
        return schedule.venue_id == target.venue_id
    if gate is ConflictGate.organizer:
        return (
            bool(target.organizer_name)
            and schedule.organizer_name == target.organizer_name
        )
    if gate is ConflictGate.coach:
        return bool(set(target.coach_names) & set(schedule.coach_names_list))
    return True


async def _load_candidates(
    db: AsyncSession, target: ScheduleTarget, member: str | None
) -> list[Event]:
    stmt = select(Event).where(Event.deleted_at.is_(None))
    if member is not None:
        stmt = stmt.join(Enrollment, Enrollment.event_id == Event.id).where(
            Enrollment.membername == member,
            Enrollment.status.in_(ENROLLED_STATUSES),
        )
    if target.exclude_event_id is not None:
        stmt = stmt.where(Event.id != target.exclude_event_id)
    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def _load_overrides(
    db: AsyncSession, event_ids: list[int]
) -> dict[int, dict[int, OccurrenceOverride]]:
    if not event_ids:
        return {}
    result = await db.execute(
        select(OccurrenceOverride).where(OccurrenceOverride.event_id.in_(event_ids))
    )
    by_event: dict[int, dict[int, OccurrenceOverride]] = {}
    for override in result.scalars().all():
        by_event.setdefault(override.event_id, {})[override.occurrence_time] = override
    return by_event


def _findings_for_gate(
    gate: ConflictGate,
    target: ScheduleTarget,
    candidates: list[Event],
    overrides: dict[int, dict[int, OccurrenceOverride]],
) -> list[Finding]:
    findings: list[Finding] = []
    for candidate in candidates:
        matching = {
            s.id for s in candidate.schedules if _resource_matches(gate, target, s)
        }
        if not matching:
            continue
        # Only occurrences produced by a schedule holding the resource clash;
        # the member gate is about the whole event.
        narrow = None if gate is ConflictGate.member else matching
        pairs = _pairs(target, candidate, overrides.get(candidate.id, {}), narrow)
        if pairs:
            findings.append(
                Finding(gate, candidate.id, candidate.title, candidate.type, pairs)
            )
    return findings


def legacy_report(findings: list[Finding]) -> ConflictReport:
    """The 409 body the create endpoints have always returned."""

    def item(f: Finding) -> VenueConflictItem:
        _, _, other_start, other_end = f.occurrences[0]
        return VenueConflictItem(
            event_id=f.event_id,
            event_title=f.event_title,
            start_time_utc=other_start,
            end_time_utc=other_end,
        )

    venue = [item(f) for f in findings if f.gate is ConflictGate.venue]
    people = [item(f) for f in findings if f.gate is not ConflictGate.venue]
    return ConflictReport(
        has_conflict=bool(venue or people), venue_conflicts=venue, user_conflicts=people
    )


async def check_conflicts(
    db: AsyncSession,
    target: ScheduleTarget,
    gates: dict[ConflictGate, GatePolicy],
) -> GateReport:
    """Run the requested gates against ``target``.

    Raises ``EventConflictException`` for a finding under a blocking gate
    where the pairing blocks; every other finding is returned.
    """
    if not gates:
        return GateReport()
    resource_gates = {g: p for g, p in gates.items() if g is not ConflictGate.member}
    candidates = await _load_candidates(db, target, None) if resource_gates else []
    member_candidates: list[Event] = []
    if ConflictGate.member in gates and target.membername is not None:
        member_candidates = await _load_candidates(db, target, target.membername)
    overrides = await _load_overrides(
        db, [c.id for c in candidates] + [c.id for c in member_candidates]
    )

    blocking: list[Finding] = []
    advisory: list[Finding] = []
    for gate, policy in gates.items():
        pool = member_candidates if gate is ConflictGate.member else candidates
        for finding in _findings_for_gate(gate, target, pool, overrides):
            blocks = (
                policy is GatePolicy.block
                and blocks_on_conflict(target.event_type)
                and blocks_on_conflict(finding.event_type)
            )
            (blocking if blocks else advisory).append(finding)
    if blocking:
        raise EventConflictException(legacy_report(blocking), findings=blocking)
    return GateReport(findings=advisory)


def target_for_event(event: Event, *, exclude_self: bool = True) -> ScheduleTarget:
    """A target describing an existing event's current schedule."""
    current = event.current_schedule
    return ScheduleTarget(
        event_type=event.type,
        venue_id=current.venue_id,
        start_time=current.start_time,
        end_time=current.end_time,
        rrule=current.rrule,
        effective_from=current.effective_from,
        effective_until=current.effective_until,
        organizer_name=current.organizer_name,
        coach_names=tuple(current.coach_names_list),
        exclude_event_id=event.id if exclude_self else None,
    )
