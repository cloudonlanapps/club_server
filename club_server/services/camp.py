"""Camps: the daily, bounded series (#384).

Holds only the camp's own rules — the daily rule with a COUNT (R17–R20),
the series cancel and its undo (R5, R5d), and the in-place reschedule a
camp allows while nothing has been recorded against it (R2, R2c).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.event import Event
from ..exceptions import (
    CancellationLeadTimeViolatedException,
    EffectiveTimeInPastException,
    EffectiveTimeNotSessionBoundaryException,
    EventAlreadyCancelledException,
    EventNotCancelledException,
    InvalidCampRruleException,
)
from ..schemas.event import Session
from ..utils import now_utc_ms
from .event import EventService
from .in_place_reschedule import reschedule_in_place
from .register_clearing import clear_cancelled_registers, marked_occurrences_at_or_after
from .rrule import parse_rrule_with_exdates
from .schedule import is_slot_of_current_schedule
from .schedule_writes import CANCELLATION_LEAD_TIME_MS


def validate_camp_rrule(rrule_string: str) -> None:
    """R17, R18, R20: daily, bounded by ``COUNT``. ``EXDATE`` lines are rest days.

    ``UNTIL`` is not a bound a caller may set — a camp's end is derived from
    its ``COUNT`` and a cutoff is written only by cancelling (R2b).
    """
    if not rrule_string or not rrule_string.strip():
        raise InvalidCampRruleException(rrule_string, "empty rrule")

    rrule_part, _exdates = parse_rrule_with_exdates(rrule_string)
    if not rrule_part:
        raise InvalidCampRruleException(rrule_string, "missing RRULE component")

    parts = {
        token.split("=", 1)[0].upper(): token.split("=", 1)[1]
        for token in rrule_part.split(";")
        if "=" in token
    }

    if parts.get("FREQ", "").upper() != "DAILY":
        raise InvalidCampRruleException(rrule_string, "FREQ must be DAILY")
    if "UNTIL" in parts:
        raise InvalidCampRruleException(
            rrule_string,
            "UNTIL is written only by cancelling; bound the camp with COUNT",
        )
    if "COUNT" not in parts:
        raise InvalidCampRruleException(rrule_string, "must include COUNT")


def validate_camp_cancellation_time(
    event: Event, chosen_ms: int, *, is_super_admin: bool
) -> bool:
    """R5c: the effective time is an occurrence start, 30 minutes ahead.

    Rule 1 (unconditional): must equal a real occurrence start.
    Rule 2: must be at least 30 minutes ahead. Rule 3: must not be past.
    Rules 2–3 are bypassable by a super admin; returns ``True`` when that
    bypass was actually used, so the caller can suppress the notification
    and record the override in the audit log.
    """
    if not is_slot_of_current_schedule(event, chosen_ms):
        raise EffectiveTimeNotSessionBoundaryException()

    now = now_utc_ms()
    in_past = chosen_ms < now
    within_lead = chosen_ms < now + CANCELLATION_LEAD_TIME_MS

    if not is_super_admin:
        if in_past:
            raise EffectiveTimeInPastException()
        if within_lead:
            raise CancellationLeadTimeViolatedException()

    return is_super_admin and (in_past or within_lead)


class CampService(EventService):
    """Cancel, undo-cancel and in-place reschedule."""

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def cancel_series(
        self,
        event_id: int,
        *,
        reason: str,
        effective_time: int,
        is_super_admin: bool = False,
        expected_version: int,
        actor: str | None = None,
    ) -> tuple[Event, bool]:
        """R5, R5a: set the cutoff. The camp runs normally up to it (L6, L9).

        Enrollment rows are not mutated: enrollment answers a different
        question and must survive a cancel so a later undo stays intact.
        The registers of occurrences at or after the cutoff are cleared
        (attendance R21c); under the ordinary rules there are none, since
        the cutoff is ahead of every open register.
        """
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        if event.cutoff is not None:
            raise EventAlreadyCancelledException(event_id)
        override_used = validate_camp_cancellation_time(
            event, effective_time, is_super_admin=is_super_admin
        )
        audience = await self.list_change_audience(event)
        now = now_utc_ms()
        event.cutoff = effective_time
        event.touch(actor, now)
        event.current_schedule.updated_at = now
        await self.db.flush()
        _ = await clear_cancelled_registers(
            self.db,
            event,
            await marked_occurrences_at_or_after(self.db, event.id, effective_time),
            actor,
        )
        if not override_used:
            await self.notify(
                "event.cancelled",
                audience,
                {
                    **self._event_facts(event),
                    "reason": reason,
                    "effectiveTimeUtc": effective_time,
                },
            )
        return event, override_used

    async def undo_cancel_series(
        self, event_id: int, *, expected_version: int, actor: str | None = None
    ) -> Event:
        """R5d: clear the cutoff and apply the undo-notification policy."""
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        if event.cutoff is None:
            raise EventNotCancelledException(event_id)
        audience = await self.list_change_audience(event)
        now = now_utc_ms()
        event.cutoff = None
        event.touch(actor, now)
        event.current_schedule.updated_at = now
        await self.db.flush()
        await self._notifications.apply_undo_notification_policy(
            audience=audience,
            cancelled_type="event.cancelled",
            restored_type="event.restored",
            match={"eventId": event.id},
            restored_data=self._event_facts(event),
        )
        return event

    async def reschedule(
        self,
        event_id: int,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        rrule: str | None = None,
        venue_id: int | None = None,
        sessions: list[Session] | None = None,
        update_sessions: bool = False,
        reset_overrides: bool = False,
        expected_version: int,
        actor: str | None = None,
    ) -> Event:
        """R2, R2c: rewrite the camp's one schedule while nothing is recorded."""
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        if event.cutoff is not None:
            raise EventAlreadyCancelledException(event_id)
        if rrule is not None:
            validate_camp_rrule(rrule)
        return await reschedule_in_place(
            self,
            event,
            start_time=start_time,
            end_time=end_time,
            rrule=rrule,
            venue_id=venue_id,
            sessions=sessions,
            update_sessions=update_sessions,
            reset_overrides=reset_overrides,
            actor=actor,
        )
