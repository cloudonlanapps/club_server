"""One-offs: a single occurrence on a single day (#384).

Holds only the one-off's own rules — no recurrence (R13, R14), drop and
reinstate (R3–R8), and the in-place reschedule that carries the same
guards as moving the occurrence (R16a, R16b, R21b).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.event import Event
from ..exceptions import (
    InvalidOneOffRruleException,
    InvalidStateException,
    PostponeOnlyException,
    RescheduleLeadTimeViolatedException,
)
from ..schemas.event import Session
from ..utils import now_utc_ms
from .conflict_gates import (
    ConflictGate,
    GatePolicy,
    check_conflicts,
    target_for_event,
)
from .event import EventService
from .in_place_reschedule import reschedule_in_place
from .schedule_writes import CANCELLATION_LEAD_TIME_MS


def validate_oneoff_rrule(rrule_string: str | None) -> None:
    """R13: a one-off carries no recurrence rule."""
    if rrule_string:
        raise InvalidOneOffRruleException(rrule_string)


class OneOffService(EventService):
    """Drop, reinstate and in-place reschedule."""

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def drop(
        self,
        event_id: int,
        *,
        reason: str,
        is_super_admin: bool,
        actor: str,
        expected_version: int,
    ) -> tuple[Event, bool]:
        """R3, R5, R5a, R8, R17: cancel the single occurrence, under one implementation.

        ``expected_version`` is the occurrence's (lifecycle L23a).
        """
        from .occurrence import OccurrenceService

        event = await self.get_live_event(event_id)
        override_used = await OccurrenceService(self.db).cancel_occurrence(
            event.id,
            event.current_schedule.start_time,
            is_super_admin=is_super_admin,
            actor=actor,
            reason=reason,
            expected_version=expected_version,
        )
        return event, override_used

    async def reinstate(
        self, event_id: int, *, expected_version: int, actor: str | None = None
    ) -> Event:
        """R6, R7: restore the dropped occurrence, only before it has started."""
        from .occurrence import OccurrenceService

        event = await self.get_live_event(event_id)
        slot = event.current_schedule.start_time
        if slot <= now_utc_ms():
            raise InvalidStateException(
                f"Event {event.id} has started; a passed occasion cannot be reinstated"
            )
        _ = await OccurrenceService(self.db).restore_occurrence(
            event.id,
            slot,
            expected_version=expected_version,
            enforce_lead_time=False,
            actor=actor,
        )
        report = await check_conflicts(
            self.db,
            target_for_event(event),
            {
                ConflictGate.venue: GatePolicy.advise,
                ConflictGate.organizer: GatePolicy.advise,
                ConflictGate.coach: GatePolicy.advise,
            },
        )
        await self.notify_conflicts(event, report.findings)
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
        """R2, R16a, R16b, R21b: the same guards as moving the occurrence."""
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        if rrule is not None:
            raise InvalidStateException("rrule cannot be set on a oneOff event")
        current = event.current_schedule
        now = now_utc_ms()
        if current.start_time < now + CANCELLATION_LEAD_TIME_MS:
            raise RescheduleLeadTimeViolatedException()
        if start_time is not None and start_time < current.start_time:
            raise PostponeOnlyException()
        return await reschedule_in_place(
            self,
            event,
            start_time=start_time,
            end_time=end_time,
            rrule=None,
            venue_id=venue_id,
            sessions=sessions,
            update_sessions=update_sessions,
            reset_overrides=reset_overrides,
            actor=actor,
        )
