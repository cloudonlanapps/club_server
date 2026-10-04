"""What every event type shares (#384, #389).

Identity, presentation and eligibility live on the event; creation,
correction, the metadata update, soft-delete and restore, and the
notifications every verb sends are here. The per-type rules live in
``programme.py``, ``camp.py`` and ``oneoff.py``, which extend this service;
``event_types.py`` is the only place a type is compared.
"""

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.event import Event
from ..db.models.event_schedule import EventSchedule
from ..db.models.user import User, UserStatus
from ..db.models.venue import Venue
from ..exceptions import (
    EventNotFoundException,
    HardDeleteNeedsSoftDeleteException,
    NothingToRestoreException,
    ScheduleNotFoundException,
    StaleVersionException,
    VenueIsDeletedException,
)
from ..schemas.common import ChangeLog, UserRoles
from ..schemas.event import Session
from ..utils import now_utc_ms
from ..validation import validate_utc_midnight
from .conflict_gates import (
    ConflictGate,
    Finding,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
    target_for_event,
)
from .event_eligibility import validate_dob_window
from .event_marketing_basic import apply_basic_marketing
from .event_types import validate_rrule_for
from .notification import NotificationEvent, NotificationService
from .schedule import first_slot
from .schedule_writes import (
    decode_sessions,
    encode_names,
    validate_coaches,
    encode_sessions,
    validate_horizon,
    validate_organizer,
    validate_timetable,
    validate_venue,
    validate_window,
)

# Enrolled-user statuses that should receive event-change notifications.
NOTIFIABLE_ENROLLMENT_STATUSES = {
    EnrollmentStatus.invited.value,
    EnrollmentStatus.requested.value,
    EnrollmentStatus.accepted.value,
    EnrollmentStatus.assigned.value,
    EnrollmentStatus.assigned_trial.value,
    EnrollmentStatus.withdraw_requested.value,
}

# Map ChangeLog field names to the notifications that should fire for them.
_VENUE_FIELDS = {"venue_id"}
_RESCHEDULE_FIELDS = {"start_time", "end_time", "rrule"}
_COACH_FIELDS = {"coach_names"}


async def list_event_change_audience(db: AsyncSession, event: Event) -> list[str]:
    """Recipients for event/occurrence cancel, restore, and reschedule notices.

    Non-terminal enrollees plus the assigned coaches and the organiser,
    deduplicated (a coach who is also enrolled receives one row, not two).
    Order is stable: enrollees, then coaches, then organiser.
    """
    result = await db.execute(
        select(Enrollment.membername).where(
            Enrollment.event_id == event.id,
            Enrollment.status.in_(NOTIFIABLE_ENROLLMENT_STATUSES),
        )
    )
    recipients: list[str] = []
    seen: set[str] = set()

    def _add(name: str | None) -> None:
        if name and name not in seen:
            seen.add(name)
            recipients.append(name)

    for membername in result.scalars().all():
        _add(membername)
    for coach in event.coach_names_list:
        _add(coach)
    _add(event.organizer_name)
    return recipients


class EventService:
    """Service for the operations every event type shares."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)

    @staticmethod
    def check_version(event: Event, expected_version: int) -> None:
        """Optimistic lock (#292): refuse a mutation carrying a version the
        event has moved past, naming who moved it and when."""
        if event.version != expected_version:
            raise StaleVersionException(
                event.version, event.updated_at, event.updated_by
            )

    # --- reads ---------------------------------------------------------

    async def get_event(self, event_id: int) -> Event:
        """Get an event by ID (includes soft-deleted)."""
        result = await self.db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)
        return event

    async def get_live_event(self, event_id: int) -> Event:
        """Get an event by ID, excluding soft-deleted ones.

        The loader every mutation uses, whatever the event's type (#471,
        lifecycle L8): a deleted event is absent until it is restored.
        """
        result = await self.db.execute(
            select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
        )
        event = result.scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)
        return event

    # --- notifications -------------------------------------------------

    def _event_facts(self, event: Event) -> dict[str, object]:
        return {"eventId": event.id, "eventTitle": event.title, "eventType": event.type}

    async def list_change_audience(self, event: Event) -> list[str]:
        """Enrollees, coaches and organiser of ``event``."""
        return await list_event_change_audience(self.db, event)

    async def list_enrolled_members(self, event_id: int) -> list[str]:
        """Users with a non-terminal enrollment on the event."""
        result = await self.db.execute(
            select(Enrollment.membername).where(
                Enrollment.event_id == event_id,
                Enrollment.status.in_(NOTIFIABLE_ENROLLMENT_STATUSES),
            )
        )
        return [row for (row,) in result.all()]

    async def list_admin_usernames(self) -> list[str]:
        """Active admins (including super-admins), for admin-facing notices."""
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None), User.status == UserStatus.active.value
            )
        )
        admins: list[str] = []
        for u in result.scalars().all():
            if u.is_super_admin:
                admins.append(u.username)
                continue
            if not u.roles:
                continue
            if "admin" in UserRoles.model_validate_json(u.roles).roles:
                admins.append(u.username)
        return admins

    async def notify(
        self, notification_type: str, recipients: list[str], data: dict[str, object]
    ) -> None:
        """Emit one notification row per recipient."""
        _ = await self._notifications.notify_for_event(
            NotificationEvent(type=notification_type, recipients=recipients, data=data)
        )

    async def notify_conflicts(
        self,
        event: Event,
        findings: Iterable[Finding],
        *,
        occurrence_time_utc: int | None = None,
    ) -> None:
        """Tell the admins about advisory findings (programme R30c, one-off R20)."""
        seen: dict[int, Finding] = {}
        for finding in findings:
            _ = seen.setdefault(finding.event_id, finding)
        if not seen:
            return
        data: dict[str, object] = {
            **self._event_facts(event),
            "conflictingEvents": [
                {
                    "eventId": f.event_id,
                    "eventTitle": f.event_title,
                    "startTimeUtc": f.occurrences[0][2],
                    "endTimeUtc": f.occurrences[0][3],
                }
                for f in seen.values()
            ],
        }
        if occurrence_time_utc is not None:
            data["occurrenceTimeUtc"] = occurrence_time_utc
        await self.notify(
            "event.conflict_detected", await self.list_admin_usernames(), data
        )

    async def notify_schedule_change(self, event: Event, changes: ChangeLog) -> None:
        """Notify enrolled members of a venue, time/rule or coach change."""
        if not changes:
            return
        changed_fields = set(changes.changes.keys())
        recipients = await self.list_enrolled_members(event.id)
        if not recipients:
            return
        facts = self._event_facts(event)
        if changed_fields & _VENUE_FIELDS:
            venue_change = changes.changes["venue_id"]
            await self.notify(
                "event.venue_changed",
                recipients,
                {
                    **facts,
                    "previousVenueId": venue_change.old,
                    "newVenueId": venue_change.new,
                },
            )
        if changed_fields & _RESCHEDULE_FIELDS:
            diff = {
                f: {"from": changes.changes[f].old, "to": changes.changes[f].new}
                for f in changed_fields & _RESCHEDULE_FIELDS
            }
            await self.notify(
                "event.rescheduled", recipients, {**facts, "changes": diff}
            )
        if changed_fields & _COACH_FIELDS:
            await self.notify(
                "event.coach_changed",
                recipients,
                {**facts, "coachNames": event.coach_names_list},
            )

    # --- creation ------------------------------------------------------

    async def create_event(
        self,
        title: str,
        event_type: str,
        visibility: str,
        venue_id: int,
        start_time: int,
        end_time: int,
        description: str | None = None,
        organizer_name: str | None = None,
        coach_names: list[str] | None = None,
        rrule: str | None = None,
        default_organizer: str | None = None,
        gender: str | None = None,
        dob_on_or_after_utc: int | None = None,
        dob_on_or_before_utc: int | None = None,
        is_featured: bool = False,
        gallery_uris: list[str] | None = None,
        sessions: list[Session] | None = None,
        short_description: str | None = None,
        stamp: str | None = None,
        highlights: list[str] | None = None,
        includes: list[str] | None = None,
    ) -> Event:
        """Create an event and its first schedule.

        Conflict detection runs on the venue, organizer and coach gates
        (programme R31, one-off R21). A programme-against-programme clash
        blocks (R30); every other finding is reported to the admins (R30c).
        """
        await validate_venue(self.db, venue_id)
        await validate_organizer(self.db, organizer_name)
        await validate_coaches(self.db, coach_names)
        validate_rrule_for(event_type, rrule)
        validate_window(start_time, end_time)
        validate_utc_midnight(dob_on_or_after_utc, "dobOnOrAfterUtc")
        validate_utc_midnight(dob_on_or_before_utc, "dobOnOrBeforeUtc")
        validate_dob_window(dob_on_or_after_utc, dob_on_or_before_utc)
        validate_timetable(sessions, start_time, end_time)

        now = now_utc_ms()
        effective_organizer = organizer_name or default_organizer
        report = await check_conflicts(
            self.db,
            ScheduleTarget(
                event_type=event_type,
                venue_id=venue_id,
                start_time=start_time,
                end_time=end_time,
                rrule=rrule,
                effective_from=start_time,
                organizer_name=effective_organizer,
                coach_names=tuple(coach_names or ()),
            ),
            {
                ConflictGate.venue: GatePolicy.block,
                ConflictGate.organizer: GatePolicy.block,
                ConflictGate.coach: GatePolicy.advise,
            },
        )

        event = Event(
            title=title,
            description=description,
            type=event_type,
            visibility=visibility,
            venue_id=venue_id,
            organizer_name=effective_organizer,
            coach_names=coach_names,
            start_time=start_time,
            end_time=end_time,
            rrule=rrule,
            gender=gender,
            dob_on_or_after_utc=dob_on_or_after_utc,
            dob_on_or_before_utc=dob_on_or_before_utc,
            is_featured=is_featured,
            gallery_uris=encode_names(gallery_uris),
            sessions=encode_sessions(sessions),
            short_description=short_description,
            stamp=stamp,
            highlights=encode_names(highlights),
            includes=encode_names(includes),
            created_at=now,
            updated_at=now,
        )
        event.updated_by = default_organizer
        first = first_slot(event)
        validate_horizon(event_type, first if first is not None else start_time, now)
        self.db.add(event)
        await self.db.flush()

        await self.notify_conflicts(event, report.findings)
        await self.db.commit()
        return event

    # --- correction and metadata update -------------------------------

    async def correct_event(
        self,
        event_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
        gender: str | None = None,
        dob_on_or_after_utc: int | None = None,
        dob_on_or_before_utc: int | None = None,
        is_featured: bool | None = None,
        gallery_uris: list[str] | None = None,
        sessions: list[Session] | None = None,
        schedule_id: int | None = None,
        short_description: str | None = None,
        stamp: str | None = None,
        highlights: list[str] | None = None,
        includes: list[str] | None = None,
        fields_set: set[str] | None = None,
        expected_version: int,
        actor: str | None = None,
    ) -> tuple[Event, ChangeLog]:
        """Correct a programme's identity, eligibility and presentation (R21, R22a, R25a).

        A correction fixes a fact that was always true and has one value, so
        it applies to every occurrence, past and future. Scheduling and
        staffing are not part of it (R22, R21a): those are a split. The
        timetable is corrected on one schedule — ``schedule_id``'s, or the
        latest (R22c–R22e).
        """
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        fields_set = fields_set or set()
        changes = ChangeLog()
        if "sessions" in fields_set:
            self._replace_timetable(
                self._schedule_of(event, schedule_id), sessions, changes
            )
        if title is not None:
            changes.add("title", event.title, title)
            event.title = title
        if description is not None:
            changes.add("description", event.description, description)
            event.description = description
        if visibility is not None:
            changes.add("visibility", event.visibility, visibility)
            event.visibility = visibility
        if "gender" in fields_set:
            changes.add("gender", event.gender, gender)
            event.gender = gender
        if "dob_on_or_after_utc" in fields_set:
            validate_utc_midnight(dob_on_or_after_utc, "dobOnOrAfterUtc")
            changes.add(
                "dob_on_or_after_utc", event.dob_on_or_after_utc, dob_on_or_after_utc
            )
            event.dob_on_or_after_utc = dob_on_or_after_utc
        if "dob_on_or_before_utc" in fields_set:
            validate_utc_midnight(dob_on_or_before_utc, "dobOnOrBeforeUtc")
            changes.add(
                "dob_on_or_before_utc", event.dob_on_or_before_utc, dob_on_or_before_utc
            )
            event.dob_on_or_before_utc = dob_on_or_before_utc
        if is_featured is not None:
            changes.add("is_featured", event.is_featured, is_featured)
            event.is_featured = is_featured
        if "gallery_uris" in fields_set:
            changes.add("gallery_uris", event.gallery_uris, encode_names(gallery_uris))
            event.gallery_uris = encode_names(gallery_uris)
        apply_basic_marketing(
            event,
            changes,
            fields_set,
            {
                "short_description": short_description,
                "stamp": stamp,
                "highlights": highlights,
                "includes": includes,
            },
        )
        validate_dob_window(event.dob_on_or_after_utc, event.dob_on_or_before_utc)
        event.touch(actor)
        await self.db.flush()
        return event, changes

    @staticmethod
    def _schedule_of(event: Event, schedule_id: int | None) -> EventSchedule:
        """The schedule ``schedule_id`` names, or the latest (programme R22c, R22d)."""
        if schedule_id is None:
            return event.current_schedule
        for schedule in event.schedules:
            if schedule.id == schedule_id:
                return schedule
        raise ScheduleNotFoundException(event.id, schedule_id)

    @staticmethod
    def _replace_timetable(
        schedule: EventSchedule, sessions: list[Session] | None, changes: ChangeLog
    ) -> None:
        """Correct one schedule's timetable, checked against its own length (R22e)."""
        validate_timetable(sessions, schedule.start_time, schedule.end_time)
        encoded = encode_sessions(sessions)
        if encoded != schedule.sessions:
            changes.add("sessions", schedule.sessions, encoded)
        schedule.sessions = encoded
        schedule.updated_at = now_utc_ms()

    async def update_event(
        self,
        event_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
        organizer_name: str | None = None,
        coach_names: list[str] | None = None,
        gender: str | None = None,
        dob_on_or_after_utc: int | None = None,
        dob_on_or_before_utc: int | None = None,
        is_featured: bool | None = None,
        gallery_uris: list[str] | None = None,
        short_description: str | None = None,
        stamp: str | None = None,
        highlights: list[str] | None = None,
        includes: list[str] | None = None,
        sessions: list[Session] | None = None,
        fields_set: set[str] | None = None,
        expected_version: int,
        actor: str | None = None,
    ) -> tuple[Event, ChangeLog]:
        """Update a camp's or one-off's metadata in place (camp R2). Returns (event, changes).

        ``sessions`` corrects the timetable of the event's one schedule, at
        any time (camp R105, one-off R22a).

        A change of organizer or coaches runs the gates creation runs (#473,
        one-off R21). A camp or one-off never blocks on a clash (programme
        R30c, one-off R20), so every finding is reported to the admins.

        For nullable fields, callers pass ``fields_set`` (the Pydantic
        ``model_fields_set`` of the request body) so this method can
        distinguish "unset" from "set to null".
        """
        event = await self.get_live_event(event_id)
        self.check_version(event, expected_version)
        fields_set = fields_set or set()
        await validate_organizer(self.db, organizer_name)
        await validate_coaches(self.db, coach_names)
        staff_before = (event.organizer_name, event.coach_names_list)
        changes = ChangeLog()
        if "sessions" in fields_set:
            self._replace_timetable(event.current_schedule, sessions, changes)
        if title is not None:
            changes.add("title", event.title, title)
            event.title = title
        if description is not None:
            changes.add("description", event.description, description)
            event.description = description
        if visibility is not None:
            changes.add("visibility", event.visibility, visibility)
            event.visibility = visibility
        if organizer_name is not None:
            changes.add("organizer_name", event.organizer_name, organizer_name)
            event.organizer_name = organizer_name
        if coach_names is not None:
            old_coaches = event.coach_names_list
            if old_coaches != coach_names:
                changes.add("coach_names", old_coaches, coach_names)
            event.set_coaches(coach_names)
        if "gender" in fields_set:
            changes.add("gender", event.gender, gender)
            event.gender = gender
        if "dob_on_or_after_utc" in fields_set:
            validate_utc_midnight(dob_on_or_after_utc, "dobOnOrAfterUtc")
            changes.add(
                "dob_on_or_after_utc", event.dob_on_or_after_utc, dob_on_or_after_utc
            )
            event.dob_on_or_after_utc = dob_on_or_after_utc
        if "dob_on_or_before_utc" in fields_set:
            validate_utc_midnight(dob_on_or_before_utc, "dobOnOrBeforeUtc")
            changes.add(
                "dob_on_or_before_utc", event.dob_on_or_before_utc, dob_on_or_before_utc
            )
            event.dob_on_or_before_utc = dob_on_or_before_utc
        if is_featured is not None:
            changes.add("is_featured", event.is_featured, is_featured)
            event.is_featured = is_featured
        if "gallery_uris" in fields_set:
            event.gallery_uris = encode_names(gallery_uris)
        apply_basic_marketing(
            event,
            changes,
            fields_set,
            {
                "short_description": short_description,
                "stamp": stamp,
                "highlights": highlights,
                "includes": includes,
            },
        )

        validate_dob_window(event.dob_on_or_after_utc, event.dob_on_or_before_utc)
        # Timetable and schedule fields move through /reschedule; the stored
        # timetable is re-checked here only so a stale one is caught early.
        validate_timetable(
            decode_sessions(event.sessions), event.start_time, event.end_time
        )

        now = now_utc_ms()
        event.touch(actor, now)
        event.current_schedule.updated_at = now
        await self.db.flush()
        if (event.organizer_name, event.coach_names_list) != staff_before:
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
        await self.notify_schedule_change(event, changes)
        return event, changes

    # --- delete and restore -------------------------------------------

    async def soft_delete_event(self, event_id: int, actor: str | None = None) -> Event:
        """Soft delete an event by setting deleted_at."""
        event = await self.get_live_event(event_id)
        recipients = await self.list_enrolled_members(event.id)
        deleted_at = now_utc_ms()
        event.deleted_at = deleted_at
        event.touch(actor, deleted_at)
        await self.db.flush()
        await self.notify(
            "event.deleted",
            recipients,
            {**self._event_facts(event), "deletedAtUtc": deleted_at},
        )
        return event

    async def restore_event(self, event_id: int, actor: str | None = None) -> Event:
        """Restore a soft-deleted event. Blocked if its venue is soft-deleted."""
        event = await self.get_event(event_id)
        if event.deleted_at is None:
            raise NothingToRestoreException("Event", event_id)
        venue = (
            await self.db.execute(select(Venue).where(Venue.id == event.venue_id))
        ).scalar_one_or_none()
        if venue and venue.deleted_at is not None:
            raise VenueIsDeletedException(event.venue_id)
        event.deleted_at = None
        event.touch(actor)
        await self.db.flush()
        await self.notify(
            "event.restored",
            await self.list_enrolled_members(event.id),
            self._event_facts(event),
        )
        return event

    async def hard_delete_event(self, event_id: int) -> dict[str, int | str]:
        """Hard delete an event. SuperAdmin only; the event must be soft-deleted.

        Related rows (schedules, enrollments, overrides, attendance) go by
        database CASCADE.
        """
        event = await self.get_event(event_id)
        if event.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("Event", event_id)
        event_title = event.title
        await self.db.delete(event)
        await self.db.flush()
        return {"title": event_title}

    async def check_user_event_access(self, event_id: int, membername: str) -> Event:
        """Get event if user has access (enrolled or event is public).

        404 for both a missing event and an inaccessible private one, so
        existence is not leaked.
        """
        event = await self.get_live_event(event_id)
        if event.visibility == "public":
            return event
        enrollment_result = await self.db.execute(
            select(Enrollment.id).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == membername,
                Enrollment.status.in_(NOTIFIABLE_ENROLLMENT_STATUSES),
            )
        )
        if enrollment_result.scalar_one_or_none() is not None:
            return event
        raise EventNotFoundException(event_id)
