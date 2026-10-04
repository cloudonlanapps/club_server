"""Occurrence listing, reschedule, cancel and restore (#384).

An occurrence is a slot of one of the event's schedules. Whether it happens
is decided by its slot — a cancelled override, or a slot at or after the
cutoff (lifecycle L14) — never by where it was moved to (programme R19b),
and ``services/lifecycle.py`` is the only place that question is answered.

The guards are the same for every type (#372, #375): a reschedule may only
postpone, and neither a reschedule nor a cancellation may happen once the
register has opened, 30 minutes before the effective start. A super-admin
may cancel retrospectively (programme R17a) and bypass the lead time; the
notification is then suppressed and the override recorded in the audit log.
"""

from datetime import datetime, timedelta, timezone
from typing import overload

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord
from ..db.models.enrollment import Enrollment
from ..db.models.event import Event
from ..db.models.event_schedule import EventSchedule
from ..db.models.occurrence_override import OccurrenceOverride
from ..db.models.user import User
from ..db.models.venue import Venue
from ..exceptions import (
    CancellationLeadTimeViolatedException,
    CancelledOccurrenceException,
    EventNotFoundException,
    InvalidSessionsException,
    InvalidStateException,
    NothingToRescheduleException,
    OccurrenceNotFoundException,
    PastOccurrenceException,
    PostponeOnlyException,
    RangeTooLargeException,
    RescheduleLeadTimeViolatedException,
    VenueNotFoundException,
)
from ..schemas.occurrence import OccurrenceResponse
from ..services.eligibility import enrollment_covers_occurrence
from ..services.event import list_event_change_audience
from ..services.notification import NotificationEvent, NotificationService
from ..utils import get_display_name, now_utc_ms
from .conflict_gates import (
    ConflictGate,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
)
from .event_types import EventVerb, offers
from .lifecycle import occurrence_is_cancelled
from .occurrence_version import (
    SCHEDULED_OVERRIDE_STATUS,
    UNCHANGED_OCCURRENCE_VERSION,
    check_occurrence_version,
    new_override,
    stamp_override,
)
from .schedule import Slot, duration_of, event_slots, schedule_for_slot
from .schedule_writes import CANCELLATION_LEAD_TIME_MS

MAX_LISTING_RANGE = timedelta(days=365)


@overload
def ms_to_datetime(ms: int) -> datetime: ...
@overload
def ms_to_datetime(ms: None) -> None: ...
@overload
def ms_to_datetime(ms: int | None) -> datetime | None: ...


def ms_to_datetime(ms: int | None) -> datetime | None:
    """Convert milliseconds since epoch to datetime."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to milliseconds since epoch."""
    return int(dt.timestamp() * 1000)


def _check_range(from_time_utc: int, to_time_utc: int) -> None:
    if ms_to_datetime(to_time_utc) - ms_to_datetime(from_time_utc) > MAX_LISTING_RANGE:
        raise RangeTooLargeException()


class OccurrenceService:
    """Service for occurrence management operations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)

    async def get_event_or_raise(self, event_id: int) -> Event:
        """Get event by ID or raise exception."""
        result = await self.db.execute(
            select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
        )
        event = result.scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)
        return event

    async def get_occurrence_override(
        self, event_id: int, occurrence_time_utc: int
    ) -> OccurrenceOverride | None:
        """Get occurrence override if exists."""
        result = await self.db.execute(
            select(OccurrenceOverride).where(
                OccurrenceOverride.event_id == event_id,
                OccurrenceOverride.occurrence_time == occurrence_time_utc,
            )
        )
        return result.scalar_one_or_none()

    def slot_or_raise(self, event: Event, occurrence_time_utc: int) -> Slot:
        """The slot at ``occurrence_time_utc``, matched against the rules (R4, R18)."""
        schedule = schedule_for_slot(event, occurrence_time_utc)
        if schedule is None:
            raise OccurrenceNotFoundException(event.id, occurrence_time_utc)
        return Slot(occurrence_time_utc, schedule)

    # --- listings ------------------------------------------------------

    async def list_occurrences(
        self,
        from_time_utc: int,
        to_time_utc: int,
        event_type: str | None = None,
        visibility: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[OccurrenceResponse]:
        """List occurrences in date range."""
        _check_range(from_time_utc, to_time_utc)
        query = select(Event).where(Event.deleted_at.is_(None))
        if event_type:
            query = query.where(Event.type == event_type)
        if visibility:
            query = query.where(Event.visibility == visibility)
        events = (await self.db.execute(query)).scalars().all()

        occurrences: list[OccurrenceResponse] = []
        for event in events:
            for slot in event_slots(event, from_time_utc, to_time_utc):
                override = await self.get_occurrence_override(event.id, slot.time)
                occurrences.append(
                    await self.build_occurrence_data(event, slot, override)
                )
        return _page(occurrences, offset, limit)

    async def list_user_occurrences(
        self,
        membername: str,
        from_time_utc: int,
        to_time_utc: int,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[OccurrenceResponse]:
        """List occurrences the user is eligible to see at occurrence time.

        A public event the user has any enrollment on is always visible
        (grandfathering); a public event with no enrollment only if the user
        matches its eligibility; a private event only for the occurrences the
        user's enrollment covers (``enrollment_covers_occurrence``).
        """
        from .event_eligibility import is_user_eligible_for_event

        _check_range(from_time_utc, to_time_utc)
        enrollments_by_event: dict[int, Enrollment] = {
            e.event_id: e
            for e in (
                await self.db.execute(
                    select(Enrollment).where(Enrollment.membername == membername)
                )
            )
            .scalars()
            .all()
        }
        user = (
            await self.db.execute(
                select(User).where(
                    User.username == membername, User.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        candidate_event_ids = list(enrollments_by_event.keys())
        events = (
            (
                await self.db.execute(
                    select(Event).where(
                        Event.deleted_at.is_(None),
                        or_(
                            Event.id.in_(candidate_event_ids)
                            if candidate_event_ids
                            else False,
                            Event.visibility == "public",
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

        now_ms = now_utc_ms()
        attendance_status_by_key: dict[tuple[int, int], str] = {
            (r.event_id, r.occurrence_time_utc): r.status
            for r in (
                await self.db.execute(
                    select(AttendanceRecord).where(
                        AttendanceRecord.membername == membername,
                        AttendanceRecord.occurrence_time_utc >= from_time_utc,
                        AttendanceRecord.occurrence_time_utc <= to_time_utc,
                    )
                )
            )
            .scalars()
            .all()
        }

        occurrences: list[OccurrenceResponse] = []
        for event in events:
            enrollment = enrollments_by_event.get(event.id)
            if (
                event.visibility == "public"
                and enrollment is None
                and user is not None
                and not is_user_eligible_for_event(user, event)
            ):
                continue
            for slot in event_slots(event, from_time_utc, to_time_utc):
                if event.visibility != "public" and not enrollment_covers_occurrence(
                    enrollment, slot.time, now_ms
                ):
                    continue
                override = await self.get_occurrence_override(event.id, slot.time)
                occurrences.append(
                    await self.build_occurrence_data(
                        event,
                        slot,
                        override,
                        attendance_status=attendance_status_by_key.get(
                            (event.id, slot.time)
                        ),
                    )
                )
        return _page(occurrences, offset, limit)

    async def get_occurrence(
        self, event_id: int, occurrence_time_utc: int
    ) -> OccurrenceResponse:
        """Get a specific occurrence."""
        event = await self.get_event_or_raise(event_id)
        slot = self.slot_or_raise(event, occurrence_time_utc)
        override = await self.get_occurrence_override(event.id, slot.time)
        return await self.build_occurrence_data(event, slot, override)

    async def build_occurrence_data(
        self,
        event: Event,
        slot: Slot,
        override: OccurrenceOverride | None = None,
        attendance_status: str | None = None,
    ) -> OccurrenceResponse:
        """Build occurrence data from its schedule and optional override.

        The series-cutoff rule applies whatever the override says (R19b):
        a slot at or after the cutoff is shown cancelled even if it was
        postponed, and a slot before it stays live even if postponed past it.
        """
        schedule: EventSchedule = slot.schedule
        start_ms, end_ms = slot.time, slot.end
        venue_id = schedule.venue_id
        organizer_name = schedule.organizer_name
        occ_status = "scheduled"
        is_rescheduled = False
        cancel_reason: str | None = None
        version = UNCHANGED_OCCURRENCE_VERSION
        updated_at: int | None = None
        updated_by: str | None = None

        if override:
            if override.new_start_time:
                start_ms = override.new_start_time
                end_ms = override.new_end_time or start_ms + duration_of(schedule)
            elif override.new_end_time:
                end_ms = override.new_end_time
            venue_id = override.new_venue_id or venue_id
            organizer_name = override.new_organizer_name or organizer_name
            occ_status = override.status
            cancel_reason = override.cancel_reason
            version = override.version
            updated_at = override.updated_at
            updated_by = override.updated_by
            is_rescheduled = (
                override.new_start_time is not None
                or override.new_end_time is not None
                or override.new_venue_id is not None
                or override.new_organizer_name is not None
            )

        if occurrence_is_cancelled(
            event, slot.time, override.status if override else None
        ):
            occ_status = "cancelled"
            cutoff = event.cutoff
            if cancel_reason is None and cutoff is not None and slot.time >= cutoff:
                cancel_reason = (
                    f"series cancelled at {ms_to_datetime(cutoff).isoformat()}"
                )

        venue_name = None
        venue = (
            await self.db.execute(select(Venue).where(Venue.id == venue_id))
        ).scalar_one_or_none()
        if venue:
            venue_name = venue.name

        organizer_display_name = None
        if organizer_name:
            organizer = (
                await self.db.execute(
                    select(User).where(User.username == organizer_name)
                )
            ).scalar_one_or_none()
            if organizer:
                organizer_display_name = get_display_name(organizer)

        return OccurrenceResponse(
            event_id=event.id,
            event_title=event.title,
            event_type=event.type,
            occurrence_time_utc=slot.time,
            start_time_utc=start_ms,
            end_time_utc=end_ms,
            venue_id=venue_id,
            venue_name=venue_name,
            organizer_name=organizer_name,
            organizer_display_name=organizer_display_name,
            status=occ_status,
            is_rescheduled=is_rescheduled,
            cancel_reason=cancel_reason,
            attendance_status=attendance_status,
            version=version,
            updated_at=updated_at,
            updated_by=updated_by,
        )

    # --- guards --------------------------------------------------------

    def _guard_mutable(
        self,
        event: Event,
        slot: Slot,
        override: OccurrenceOverride | None,
        *,
        is_super_admin: bool,
        lead_exception: type[Exception] | None,
    ) -> bool:
        """The guards shared by reschedule, cancel and restore; returns ``override_used``.

        A slot at or after the cutoff does not happen and cannot be touched
        (R19b). A past slot is refused to everyone but a super-admin
        (R17a); a slot whose register has opened is refused unless a
        super-admin bypasses the lead time. ``lead_exception=None`` applies
        no lead time (a one-off's reinstate, one-off R7).
        """
        if occurrence_is_cancelled(event, slot.time, None):
            raise CancelledOccurrenceException()
        now = now_utc_ms()
        effective_start = (
            override.new_start_time
            if override is not None and override.new_start_time is not None
            else slot.time
        )
        in_past = effective_start < now
        within_lead = (
            lead_exception is not None
            and effective_start < now + CANCELLATION_LEAD_TIME_MS
        )
        if not is_super_admin:
            if in_past:
                raise PastOccurrenceException()
            if within_lead and lead_exception is not None:
                raise lead_exception()
        return is_super_admin and (in_past or within_lead)

    # --- mutations -----------------------------------------------------

    async def reschedule_occurrence(
        self,
        event_id: int,
        occurrence_time_utc: int,
        new_start_time: int | None = None,
        new_duration_minutes: int | None = None,
        new_venue_id: int | None = None,
        is_super_admin: bool = False,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> bool:
        """Postpone an occurrence, change its duration, or move its venue.

        At least one of the three must be supplied. The new start may not be
        earlier than the occurrence's current effective start (R19a), and a
        schedule that carries a timetable does not let one occurrence change
        its duration (R20b). Returns ``override_used``.

        A type without the verb — a one-off — is refused: it is moved by
        its own reschedule, which moves the schedule itself (#472).
        """
        if (
            new_start_time is None
            and new_duration_minutes is None
            and new_venue_id is None
        ):
            raise NothingToRescheduleException()

        event = await self.get_event_or_raise(event_id)
        if not offers(event, EventVerb.reschedule_occurrence):
            raise InvalidStateException(
                "This event's occurrence is moved with /reschedule, not one occurrence at a time"
            )
        slot = self.slot_or_raise(event, occurrence_time_utc)
        override = await self.get_occurrence_override(event.id, slot.time)
        check_occurrence_version(override, expected_version)
        if override is not None and override.status == "cancelled":
            raise CancelledOccurrenceException()
        override_used = self._guard_mutable(
            event,
            slot,
            override,
            is_super_admin=is_super_admin,
            lead_exception=RescheduleLeadTimeViolatedException,
        )
        current_start = (
            override.new_start_time
            if override is not None and override.new_start_time is not None
            else slot.time
        )
        if new_start_time is not None and new_start_time < current_start:
            if not is_super_admin:
                raise PostponeOnlyException()
            override_used = True
        if (
            new_duration_minutes is not None
            and slot.schedule.sessions
            and new_duration_minutes * 60_000 != duration_of(slot.schedule)
        ):
            raise InvalidSessionsException(
                "an occurrence of a schedule with a timetable keeps the timetable's length",
                code="INVALID_SESSIONS",
            )
        if new_venue_id:
            venue = (
                await self.db.execute(
                    select(Venue).where(
                        Venue.id == new_venue_id, Venue.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if not venue:
                raise VenueNotFoundException(new_venue_id)

        new_end_time: int | None = None
        if new_start_time is not None or new_duration_minutes is not None:
            effective_start = (
                new_start_time if new_start_time is not None else current_start
            )
            duration_ms = (
                new_duration_minutes * 60_000
                if new_duration_minutes is not None
                else duration_of(slot.schedule)
            )
            new_end_time = effective_start + duration_ms

        if override:
            if new_start_time is not None:
                override.new_start_time = new_start_time
            if new_end_time is not None:
                override.new_end_time = new_end_time
            if new_venue_id:
                override.new_venue_id = new_venue_id
            override.status = "rescheduled"
            stamp_override(override, actor)
        else:
            override = new_override(
                event.id,
                slot.time,
                actor,
                status="rescheduled",
                new_start_time=new_start_time,
                new_end_time=new_end_time,
                new_venue_id=new_venue_id,
            )
            self.db.add(override)
        await self.db.flush()

        await self._report_occurrence_clash(event, slot, override)

        changes: dict[str, object] = {}
        if new_start_time is not None:
            changes["startTimeUtc"] = new_start_time
        if new_duration_minutes is not None:
            changes["durationMinutes"] = new_duration_minutes
        if new_venue_id:
            changes["venueId"] = new_venue_id
        if not override_used:
            await self._notifications.notify_for_event(
                NotificationEvent(
                    type="occurrence.rescheduled",
                    recipients=await list_event_change_audience(self.db, event),
                    data={
                        "eventId": event.id,
                        "eventTitle": event.title,
                        "occurrenceTimeUtc": slot.time,
                        "changes": changes,
                    },
                )
            )
        return override_used

    async def _report_occurrence_clash(
        self, event: Event, slot: Slot, override: OccurrenceOverride
    ) -> None:
        """R31a: a moved occurrence is checked against occurrences and reported."""
        from .event import EventService

        start = override.new_start_time or slot.time
        end = override.new_end_time or start + duration_of(slot.schedule)
        report = await check_conflicts(
            self.db,
            ScheduleTarget(
                event_type=event.type,
                venue_id=override.new_venue_id or slot.schedule.venue_id,
                start_time=start,
                end_time=end,
                rrule=None,
                effective_from=start,
                effective_until=end,
                organizer_name=slot.schedule.organizer_name,
                coach_names=tuple(event.coach_names_list),
                exclude_event_id=event.id,
            ),
            {
                ConflictGate.venue: GatePolicy.advise,
                ConflictGate.organizer: GatePolicy.advise,
                ConflictGate.coach: GatePolicy.advise,
            },
        )
        await EventService(self.db).notify_conflicts(
            event, report.findings, occurrence_time_utc=slot.time
        )

    async def cancel_occurrence(
        self,
        event_id: int,
        occurrence_time_utc: int,
        is_super_admin: bool = False,
        actor: str | None = None,
        reason: str | None = None,
        *,
        expected_version: int,
    ) -> bool:
        """Cancel an occurrence (R16, R17, R17a; camp R82, R82a; one-off R3, R5).

        Returns ``override_used`` — ``True`` when a super admin bypassed the
        lead-time or past rule, in which case the notification is suppressed.
        """
        event = await self.get_event_or_raise(event_id)
        slot = self.slot_or_raise(event, occurrence_time_utc)
        override = await self.get_occurrence_override(event.id, slot.time)
        check_occurrence_version(override, expected_version)
        if override is not None and override.status == "cancelled":
            raise CancelledOccurrenceException()
        override_used = self._guard_mutable(
            event,
            slot,
            override,
            is_super_admin=is_super_admin,
            lead_exception=CancellationLeadTimeViolatedException,
        )

        if override:
            override.status = "cancelled"
            override.cancel_reason = reason
            stamp_override(override, actor)
        else:
            override = new_override(
                event.id, slot.time, actor, status="cancelled", cancel_reason=reason
            )
            self.db.add(override)
        await self.db.flush()

        # A cancelled session costs nothing (#294, R48) and keeps no register
        # (#337, attendance R21b): refund whoever paid, then drop the marks.
        from .register_clearing import clear_cancelled_registers

        _ = await clear_cancelled_registers(self.db, event, [slot.time], actor)

        if not override_used:
            await self._notifications.notify_for_event(
                NotificationEvent(
                    type="occurrence.cancelled",
                    recipients=await list_event_change_audience(self.db, event),
                    data={
                        "eventId": event.id,
                        "eventTitle": event.title,
                        "occurrenceTimeUtc": slot.time,
                        "reason": reason,
                    },
                )
            )
        return override_used

    async def restore_occurrence(
        self,
        event_id: int,
        occurrence_time_utc: int,
        *,
        expected_version: int,
        is_super_admin: bool = False,
        enforce_lead_time: bool = True,
        actor: str | None = None,
    ) -> bool:
        """Restore a cancelled occurrence (R16; one-off R6). Returns ``override_used``.

        Cancel's guards apply (#474): a past occurrence, or one inside the
        lead time, is restored only by a super-admin, and such an override
        restore sends no notification. A one-off's reinstate passes
        ``enforce_lead_time=False``: it may be reinstated until it starts
        (one-off R7).

        The occurrence comes back **unmarked** (attendance R21d): its
        register went with the cancellation, and staff mark it again if the
        session goes ahead. The row is kept even when nothing else is
        overridden, so the version never goes backwards (lifecycle L23b).
        """
        event = await self.get_event_or_raise(event_id)
        slot = self.slot_or_raise(event, occurrence_time_utc)
        override = await self.get_occurrence_override(event.id, slot.time)
        check_occurrence_version(override, expected_version)
        if not override or override.status != "cancelled":
            raise InvalidStateException("Occurrence is not cancelled")
        override_used = self._guard_mutable(
            event,
            slot,
            override,
            is_super_admin=is_super_admin,
            lead_exception=(
                CancellationLeadTimeViolatedException if enforce_lead_time else None
            ),
        )

        if (
            override.new_start_time
            or override.new_venue_id
            or override.new_organizer_name
        ):
            override.status = "rescheduled"
        else:
            override.status = SCHEDULED_OVERRIDE_STATUS
        override.cancel_reason = None
        stamp_override(override, actor)
        await self.db.flush()

        if not override_used:
            await self._notifications.apply_undo_notification_policy(
                audience=await list_event_change_audience(self.db, event),
                cancelled_type="occurrence.cancelled",
                restored_type="occurrence.restored",
                match={"eventId": event.id, "occurrenceTimeUtc": slot.time},
                restored_data={
                    "eventId": event.id,
                    "eventTitle": event.title,
                    "occurrenceTimeUtc": slot.time,
                },
            )
        return override_used


def _page(
    occurrences: list[OccurrenceResponse], offset: int, limit: int | None
) -> list[OccurrenceResponse]:
    occurrences.sort(key=lambda x: x.start_time_utc)
    if offset > 0:
        occurrences = occurrences[offset:]
    if limit is not None:
        occurrences = occurrences[:limit]
    return occurrences
