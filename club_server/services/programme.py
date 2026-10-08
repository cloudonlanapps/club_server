"""Programmes: the weekly, optionally open-ended series (#384).

Holds only the programme's own rules — its recurrence shape (R11–R15), the
cutoff a terminate, extend or split names (R2–R4), and the three verbs that
act on the current schedule (R1, R6, R8, R9, R23). A programme is never
rescheduled in place (R26): every change to its timetable is a split.
"""

import re


from ..db.models.event import Event
from ..db.models.event_schedule import EventSchedule
from ..exceptions import (
    CutoffTooSoonException,
    InvalidRruleException,
    EffectiveTimeNotSessionBoundaryException,
    InvalidProgrammeRruleException,
    InvalidStateException,
)
from ..schemas.common import ChangeLog
from .register_clearing import clear_cancelled_registers, marked_occurrences_at_or_after
from ..schemas.event import Session
from ..utils import now_utc_ms
from .conflict_gates import (
    ConflictGate,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
)
from .event import EventService
from .rrule import parse_rrule_with_exdates, validate_rrule
from .schedule import is_slot_of_current_schedule
from .schedule_writes import (
    CANCELLATION_LEAD_TIME_MS,
    decode_sessions,
    validate_coaches,
    encode_sessions,
    validate_organizer,
    validate_timetable,
    validate_venue,
    validate_window,
)

_ALLOWED_RULE_KEYS = {"FREQ", "BYDAY"}
_WEEKDAY = re.compile(r"^(MO|TU|WE|TH|FR|SA|SU)$")


def validate_programme_rrule(rrule_string: str | None) -> None:
    """R11–R15: weekly, naming its days, with no COUNT, UNTIL, EXDATE or INTERVAL.

    A missing rule is a programme of one occurrence and is accepted as such.
    """
    if rrule_string is None:
        return
    if not validate_rrule(rrule_string):
        raise InvalidRruleException(rrule_string)
    rule_part, exdates = parse_rrule_with_exdates(rrule_string)
    if exdates or "EXDATE" in rrule_string.upper():
        raise InvalidProgrammeRruleException(rrule_string, "EXDATE is not allowed")
    parts = {
        token.split("=", 1)[0].upper(): token.split("=", 1)[1]
        for token in rule_part.split(";")
        if "=" in token
    }
    if parts.get("FREQ", "").upper() != "WEEKLY":
        raise InvalidProgrammeRruleException(rrule_string, "FREQ must be WEEKLY")
    if "COUNT" in parts:
        raise InvalidProgrammeRruleException(rrule_string, "COUNT is not allowed")
    if "UNTIL" in parts:
        raise InvalidProgrammeRruleException(
            rrule_string, "UNTIL is written only by terminate, extend and split"
        )
    if "INTERVAL" in parts:
        raise InvalidProgrammeRruleException(rrule_string, "INTERVAL is not allowed")
    days = [d.strip().upper() for d in parts.get("BYDAY", "").split(",") if d.strip()]
    if not days or not all(_WEEKDAY.match(d) for d in days):
        raise InvalidProgrammeRruleException(rrule_string, "BYDAY must name weekdays")
    extra = set(parts) - _ALLOWED_RULE_KEYS
    if extra:
        raise InvalidProgrammeRruleException(
            rrule_string, f"unsupported rule parts: {sorted(extra)}"
        )


def validate_cutoff(event: Event, cutoff_ms: int, now_ms: int) -> None:
    """R2–R4: an occurrence start of the current schedule, 30 minutes ahead."""
    if not is_slot_of_current_schedule(event, cutoff_ms):
        raise EffectiveTimeNotSessionBoundaryException()
    if cutoff_ms < now_ms + CANCELLATION_LEAD_TIME_MS:
        raise CutoffTooSoonException()


class ProgrammeService(EventService):
    """Terminate, extend, extend-indefinitely and split."""

    async def terminate(
        self,
        event_id: int,
        *,
        reason: str,
        cutoff_ms: int,
        expected_version: int,
        actor: str | None = None,
    ) -> tuple[Event, int | None]:
        """R1–R5: close the current schedule at ``cutoff_ms`` and insert nothing.

        Returns the event and the cutoff it had before (always ``None`` here:
        a bounded programme is extended, not terminated again). Registers at
        or after the cutoff are cleared (attendance R21c); the cutoff is
        ahead of every open register, so in practice there are none.
        """
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        if event.cutoff is not None:
            raise InvalidStateException(
                f"Event {event.id} already has a cutoff; use extend to move it"
            )
        now = now_utc_ms()
        validate_cutoff(event, cutoff_ms, now)
        audience = await self.list_change_audience(event)
        before = event.cutoff
        event.cutoff = cutoff_ms
        event.touch(actor, now)
        event.current_schedule.updated_at = now
        await self.db.flush()
        _ = await clear_cancelled_registers(
            self.db,
            event,
            await marked_occurrences_at_or_after(self.db, event.id, cutoff_ms),
            actor,
        )
        await self.notify(
            "event.terminated",
            audience,
            {**self._event_facts(event), "reason": reason, "cutoffTimeUtc": cutoff_ms},
        )
        return event, before

    async def extend(
        self,
        event_id: int,
        *,
        cutoff_ms: int | None,
        reason: str | None,
        expected_version: int,
        actor: str | None = None,
    ) -> tuple[Event, int | None]:
        """R6–R8: move the cutoff, or clear it when ``cutoff_ms`` is ``None``.

        Only while the current cutoff is still ahead (R6a): once it has
        passed the bound balances have been released and cannot be recalled.
        """
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        before = event.cutoff
        now = now_utc_ms()
        if before is None:
            raise InvalidStateException(f"Event {event.id} has no cutoff to extend")
        if before <= now:
            raise InvalidStateException(
                f"Event {event.id} ended at its cutoff; create a new programme"
            )
        if cutoff_ms is not None:
            validate_cutoff(event, cutoff_ms, now)
        audience = await self.list_change_audience(event)
        event.cutoff = cutoff_ms
        event.touch(actor, now)
        event.current_schedule.updated_at = now
        await self.db.flush()
        await self.notify(
            "event.extended",
            audience,
            {
                **self._event_facts(event),
                "reason": reason,
                "previousCutoffUtc": before,
                "cutoffTimeUtc": cutoff_ms,
            },
        )
        return event, before

    async def split(
        self,
        event_id: int,
        *,
        cutoff_ms: int,
        venue_id: int | None = None,
        organizer_name: str | None = None,
        coach_names: list[str] | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        rrule: str | None = None,
        sessions: list[Session] | None = None,
        update_sessions: bool = False,
        expected_version: int,
        actor: str | None = None,
    ) -> tuple[Event, ChangeLog]:
        """R23–R24a: close the current schedule at the cutoff and open the next.

        The programme, its id, its enrollments, attendance, overrides and
        credit are untouched (R24d). The new schedule inherits whatever the
        request leaves unset, and is conflict-checked before it is written
        (R31): a clash with another programme blocks.
        """
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        now = now_utc_ms()
        validate_cutoff(event, cutoff_ms, now)
        current = event.current_schedule

        new_venue = venue_id if venue_id is not None else current.venue_id
        new_organizer = (
            organizer_name if organizer_name is not None else current.organizer_name
        )
        new_coaches = (
            coach_names if coach_names is not None else current.coach_names_list
        )
        new_start = start_time if start_time is not None else current.start_time
        new_end = end_time if end_time is not None else current.end_time
        if start_time is not None and end_time is None:
            new_end = current.end_time + (start_time - current.start_time)
        new_rrule = rrule if rrule is not None else current.rrule
        if update_sessions:
            new_sessions = encode_sessions(sessions)
        else:
            new_sessions = current.sessions

        if venue_id is not None:
            await validate_venue(self.db, venue_id)
        if organizer_name is not None:
            await validate_organizer(self.db, organizer_name)
        if coach_names is not None:
            await validate_coaches(self.db, coach_names)
        if rrule is not None:
            validate_programme_rrule(rrule)
        validate_window(new_start, new_end)
        validate_timetable(decode_sessions(new_sessions), new_start, new_end)

        changes = ChangeLog()
        for name, old, new in (
            ("venue_id", current.venue_id, new_venue),
            ("organizer_name", current.organizer_name, new_organizer),
            ("coach_names", current.coach_names_list, new_coaches),
            ("start_time", current.start_time, new_start),
            ("end_time", current.end_time, new_end),
            ("rrule", current.rrule, new_rrule),
            ("sessions", current.sessions, new_sessions),
        ):
            if old != new:
                changes.add(name, old, new)

        _ = await check_conflicts(
            self.db,
            ScheduleTarget(
                event_type=event.type,
                venue_id=new_venue,
                start_time=new_start,
                end_time=new_end,
                rrule=new_rrule,
                effective_from=cutoff_ms,
                effective_until=None,
                organizer_name=new_organizer,
                coach_names=tuple(new_coaches),
                exclude_event_id=event.id,
            ),
            {
                ConflictGate.venue: GatePolicy.block,
                ConflictGate.organizer: GatePolicy.block,
                ConflictGate.coach: GatePolicy.advise,
            },
        )

        audience = await self.list_change_audience(event)
        current.effective_until = cutoff_ms
        current.updated_at = now
        successor = EventSchedule(
            effective_from=cutoff_ms,
            effective_until=None,
            start_time=new_start,
            end_time=new_end,
            rrule=new_rrule,
            venue_id=new_venue,
            organizer_name=new_organizer,
            sessions=new_sessions,
            created_at=now,
            updated_at=now,
        )
        successor.set_coaches(new_coaches)
        event.schedules.append(successor)
        event.touch(actor, now)
        await self.db.flush()

        await self.notify(
            "event.split",
            audience,
            {
                **self._event_facts(event),
                "cutoffTimeUtc": cutoff_ms,
                "changes": changes.to_audit_dict(),
            },
        )
        return event, changes
