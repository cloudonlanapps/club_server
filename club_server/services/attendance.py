"""Attendance service for attendance management operations."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord, AttendanceStatus
from ..db.models.enrollment import Enrollment
from ..db.models.event import Event
from ..db.models.occurrence_override import OccurrenceOverride
from ..db.models.user import User, UserStatus
from ..exceptions import (
    AttendanceNotFoundException,
    AttendanceNotYetOpenException,
    CancelledOccurrenceException,
    EditWindowClosedException,
    EventNotFoundException,
    InvalidAttendanceStatusException,
    InvalidOccurrenceTimeException,
    InvalidStateException,
    LeaveAlreadyDeclaredException,
    LeaveWindowClosedException,
    RangeTooLargeException,
    UserNotFoundException,
)
from ..schemas.common import UserRoles
from ..services.credit_charge import CreditChargeService
from ..services.eligibility import (
    ACTIVE_ENROLLMENT_STATUSES,
    enrollment_covers_occurrence,
)
from ..services.lifecycle import is_occurrence_cancelled
from ..services.notification import NotificationEvent, NotificationService
from ..services.schedule import schedule_for_slot
from ..utils import now_utc_ms


LEAVE_DECLARATION_WINDOW_MS = 2 * 60 * 60 * 1000  # 2 hours
ATTENDANCE_EDIT_WINDOW_MS = 15 * 24 * 60 * 60 * 1000  # 15 days
ATTENDANCE_OPEN_LEAD_MS = 30 * 60 * 1000  # 30 minutes

ADMIN_MARKABLE_STATUSES = {
    AttendanceStatus.present.value,
    AttendanceStatus.absent.value,
    AttendanceStatus.late.value,
}


class AttendanceService:
    """Service for attendance management operations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)
        # Credit is reconciled after every attendance transition (#294,
        # R44a). Inert where the deployment does not run on credits.
        self._credit: CreditChargeService = CreditChargeService(db)

    async def _list_staff_usernames(self) -> list[str]:
        """Recipients for staff-facing attendance events (admins+coaches+super)."""
        result = await self.db.execute(
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
            roles = set(parsed.roles)
            if "admin" in roles or "coach" in roles:
                staff.append(u.username)
        return staff

    def _attendance_facts(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        **extra: object,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "eventId": event_id,
            "occurrenceTimeUtc": occurrence_time_utc,
            "memberUsername": membername,
        }
        data.update(extra)
        return data

    async def get_user_or_raise(self, membername: str) -> User:
        """Get user by username or raise exception."""
        result = await self.db.execute(
            select(User).where(User.username == membername, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(membername)
        return user

    async def get_effective_start(self, event_id: int, occurrence_time_utc: int) -> int:
        # OccurrenceOverride.new_start_time wins over the RRULE-derived slot key.
        # The slot key is the stable PK; the effective start is what humans see.
        result = await self.db.execute(
            select(OccurrenceOverride).where(
                OccurrenceOverride.event_id == event_id,
                OccurrenceOverride.occurrence_time == occurrence_time_utc,
            )
        )
        override = result.scalar_one_or_none()
        if override is not None and override.new_start_time is not None:
            return override.new_start_time
        return occurrence_time_utc

    async def check_edit_window(
        self, event_id: int, occurrence_time_utc: int, is_super_admin: bool
    ) -> None:
        """Check if attendance can be edited within the 15-day window."""
        if is_super_admin:
            return
        effective_start = await self.get_effective_start(event_id, occurrence_time_utc)
        window_end = effective_start + ATTENDANCE_EDIT_WINDOW_MS
        if now_utc_ms() > window_end:
            raise EditWindowClosedException()

    async def check_leave_window(
        self, event_id: int, occurrence_time_utc: int, is_super_admin: bool = False
    ) -> None:
        """Check if leave can be declared (at least 2 hours before occurrence)."""
        if is_super_admin:
            return
        effective_start = await self.get_effective_start(event_id, occurrence_time_utc)
        cutoff = effective_start - LEAVE_DECLARATION_WINDOW_MS
        if now_utc_ms() > cutoff:
            raise LeaveWindowClosedException()

    async def check_open_window(
        self, event_id: int, occurrence_time_utc: int, is_super_admin: bool
    ) -> None:
        """Reject attendance marking earlier than 30 min before effective start.

        Sudo deliberately does not bypass this gate: marking attendance before
        the session has started is data invention, not audit correction.
        """
        _ = is_super_admin
        effective_start = await self.get_effective_start(event_id, occurrence_time_utc)
        opens_at = effective_start - ATTENDANCE_OPEN_LEAD_MS
        if now_utc_ms() < opens_at:
            raise AttendanceNotYetOpenException()

    async def live_occurrence_event(
        self, event_id: int, occurrence_time_utc: int
    ) -> Event:
        """The live event, provided ``occurrence_time_utc`` is one of its slots (#470).

        A soft-deleted event is absent (404); a time the expanded schedule
        does not produce is refused (422 ``INVALID_OCCURRENCE_TIME``), so no
        record is written for a session that does not exist.
        """
        event = (
            await self.db.execute(
                select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if event is None:
            raise EventNotFoundException(event_id)
        if schedule_for_slot(event, occurrence_time_utc) is None:
            raise InvalidOccurrenceTimeException(event_id, occurrence_time_utc)
        return event

    async def reject_if_cancelled(
        self, event_id: int, occurrence_time_utc: int
    ) -> None:
        """Reject attendance mutations on cancelled occurrences (R21, lifecycle L14).

        Both routes count — a cancelled override, or a slot at or after the
        series cutoff — and ``is_occurrence_cancelled`` in
        ``services/lifecycle.py`` is the one place that answers it.
        """
        event = (
            await self.db.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none()
        if event is None:
            return
        if await is_occurrence_cancelled(self.db, event, occurrence_time_utc):
            raise CancelledOccurrenceException()

    async def check_enrollment_eligible(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Verify the member's enrollment covers `occurrence_time_utc`."""
        if is_super_admin:
            return
        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == membername,
            )
        )
        enrollment = result.scalar_one_or_none()
        if not enrollment_covers_occurrence(
            enrollment, occurrence_time_utc, now_utc_ms()
        ):
            raise InvalidStateException("user not enrolled at occurrence time")

    async def mark_attendance(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        attendance_status: str,
        notes: str | None = None,
        is_super_admin: bool = False,
        actor: str | None = None,
    ) -> bool:
        """Mark attendance for a single user.

        Where the deployment runs on credits, the member must be able to pay
        for the session. That is checked *before* the record is written and
        in the same transaction (#294, R41a), so a refusal leaves no record
        behind.

        Returns True when this mark spent the last of the member's trial
        credit and so ended their enrollment (credit R52).
        """
        if attendance_status not in ADMIN_MARKABLE_STATUSES:
            raise InvalidAttendanceStatusException(attendance_status)
        event = await self.live_occurrence_event(event_id, occurrence_time_utc)
        _ = await self.get_user_or_raise(membername)
        await self.reject_if_cancelled(event_id, occurrence_time_utc)
        await self.check_enrollment_eligible(
            event_id, occurrence_time_utc, membername, is_super_admin
        )
        await self._credit.ensure_can_charge(event, occurrence_time_utc, membername)

        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        existing = result.scalar_one_or_none()

        now = now_utc_ms()
        if existing:
            existing.previous_status = existing.status
            existing.status = attendance_status
            existing.notes = notes
            existing.recorded_at = now
        else:
            record = AttendanceRecord(
                event_id=event_id,
                occurrence_time_utc=occurrence_time_utc,
                membername=membername,
                status=attendance_status,
                notes=notes,
                recorded_at=now,
            )
            self.db.add(record)

        await self.db.flush()

        trial_ended = await self._credit.reconcile(
            event_id, occurrence_time_utc, membername, actor
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="attendance.marked",
                recipients=[membername],
                data=self._attendance_facts(
                    event_id, occurrence_time_utc, membername, status=attendance_status
                ),
            )
        )
        return trial_ended

    async def clear_attendance(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        actor: str | None = None,
    ) -> None:
        """Clear a marked attendance record, returning the member to unrecorded.

        Only real attendance marks (present/absent/late) are cleared; leave
        records (onLeave/onLeaveRequested) are managed through the leave flow
        and are left untouched. Enrollment eligibility is deliberately *not*
        checked: an admin may need to clear a record precisely because the
        member is no longer eligible for the occurrence.
        """
        _ = await self.get_user_or_raise(membername)
        await self.reject_if_cancelled(event_id, occurrence_time_utc)

        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        record = result.scalar_one_or_none()
        if not record:
            raise AttendanceNotFoundException(event_id, occurrence_time_utc, membername)

        if record.status not in ADMIN_MARKABLE_STATUSES:
            raise InvalidStateException(
                "Cannot clear a leave record; use the leave flow instead"
            )

        await self.db.delete(record)
        await self.db.flush()

        _ = await self._credit.reconcile(
            event_id, occurrence_time_utc, membername, actor
        )

    async def get_occurrence_attendance(
        self, event_id: int, occurrence_time_utc: int
    ) -> list[AttendanceRecord]:
        """Get attendance records for an occurrence."""
        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
            )
        )
        return list(result.scalars().all())

    async def get_user_attendance(
        self, event_id: int, occurrence_time_utc: int, membername: str
    ) -> AttendanceRecord | None:
        """Get a single user's attendance record for an occurrence."""
        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        return result.scalar_one_or_none()

    async def get_user_attendance_records(
        self,
        membername: str,
        from_time_utc: int,
        to_time_utc: int,
    ) -> list[AttendanceRecord]:
        """A member's own attendance records, filtered to what their
        enrollment covers (R37, #379).

        The candidate events are the union of both questions coverage asks:
        a non-terminal status covers present and future occurrences, and a
        stint bounded by ``enrolled_at``/``withdrawn_at`` covers past ones.
        Each record then passes through ``enrollment_covers_occurrence``, so a
        withdrawn member keeps the history they attended and a mid-series
        joiner never sees records predating their enrollment (R62a).
        """
        from_time = datetime.fromtimestamp(from_time_utc / 1000, tz=timezone.utc)
        to_time = datetime.fromtimestamp(to_time_utc / 1000, tz=timezone.utc)
        if to_time - from_time > timedelta(days=365):
            raise RangeTooLargeException()

        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.membername == membername,
                or_(
                    Enrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
                    and_(
                        Enrollment.enrolled_at.isnot(None),
                        Enrollment.enrolled_at <= to_time_utc,
                        or_(
                            Enrollment.withdrawn_at.is_(None),
                            Enrollment.withdrawn_at >= from_time_utc,
                        ),
                    ),
                ),
            )
        )
        enrollments = {e.event_id: e for e in result.scalars().all()}
        if not enrollments:
            return []
        records = await self.get_attendance_records(
            from_time_utc=from_time_utc,
            to_time_utc=to_time_utc,
            event_ids=list(enrollments),
            membername=membername,
        )
        now = now_utc_ms()
        return [
            r
            for r in records
            if enrollment_covers_occurrence(
                enrollments.get(r.event_id), r.occurrence_time_utc, now
            )
        ]

    async def get_attendance_records(
        self,
        from_time_utc: int,
        to_time_utc: int,
        event_ids: list[int] | None = None,
        membername: str | None = None,
    ) -> list[AttendanceRecord]:
        """Get attendance records in a date range with optional filters."""
        from_time = datetime.fromtimestamp(from_time_utc / 1000, tz=timezone.utc)
        to_time = datetime.fromtimestamp(to_time_utc / 1000, tz=timezone.utc)

        max_range = timedelta(days=365)
        if to_time - from_time > max_range:
            raise RangeTooLargeException()

        query = select(AttendanceRecord).where(
            AttendanceRecord.occurrence_time_utc >= from_time_utc,
            AttendanceRecord.occurrence_time_utc <= to_time_utc,
        )

        if event_ids is not None:
            query = query.where(AttendanceRecord.event_id.in_(event_ids))

        if membername is not None:
            query = query.where(AttendanceRecord.membername == membername)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def declare_leave(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        reason: str | None,
        is_super_admin: bool = False,
    ) -> None:
        """Declare leave for an occurrence of a live event (#470)."""
        _ = await self.live_occurrence_event(event_id, occurrence_time_utc)
        _ = await self.get_user_or_raise(membername)
        await self.reject_if_cancelled(event_id, occurrence_time_utc)
        await self.check_enrollment_eligible(
            event_id, occurrence_time_utc, membername, is_super_admin
        )
        await self.check_leave_window(event_id, occurrence_time_utc, is_super_admin)

        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        existing = result.scalar_one_or_none()

        now = now_utc_ms()
        if existing:
            if existing.status in (
                AttendanceStatus.on_leave.value,
                AttendanceStatus.on_leave_requested.value,
            ):
                raise LeaveAlreadyDeclaredException()
            existing.previous_status = existing.status
            existing.status = AttendanceStatus.on_leave_requested.value
            existing.leave_reason = reason
            existing.recorded_at = now
            record = existing
        else:
            record = AttendanceRecord(
                event_id=event_id,
                occurrence_time_utc=occurrence_time_utc,
                membername=membername,
                status=AttendanceStatus.on_leave_requested.value,
                leave_reason=reason,
                recorded_at=now,
            )
            self.db.add(record)

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="attendance.correction_requested",
                recipients=await self._list_staff_usernames(),
                data=self._attendance_facts(
                    event_id, occurrence_time_utc, membername, reason=reason
                ),
                pending_action_type="attendance_correction",
                pending_action_id=record.id,
            )
        )

    async def cancel_leave(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Cancel a leave request."""
        await self.check_enrollment_eligible(
            event_id, occurrence_time_utc, membername, is_super_admin
        )
        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        record = result.scalar_one_or_none()

        if not record:
            raise AttendanceNotFoundException(event_id, occurrence_time_utc, membername)

        if record.status != AttendanceStatus.on_leave_requested.value:
            raise InvalidStateException("Can only cancel pending leave requests")

        record_id = record.id
        await self.db.delete(record)
        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "attendance_correction", pending_action_id=record_id
        )

    async def approve_leave(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        is_super_admin: bool = False,
        actor: str | None = None,
    ) -> None:
        """Approve a leave request.

        Approved leave costs nothing, so where the session had already been
        charged the credit is returned to the account that paid it (#294,
        R45, R47).
        """
        await self.reject_if_cancelled(event_id, occurrence_time_utc)
        await self.check_enrollment_eligible(
            event_id, occurrence_time_utc, membername, is_super_admin
        )
        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        record = result.scalar_one_or_none()

        if not record:
            raise AttendanceNotFoundException(event_id, occurrence_time_utc, membername)

        if record.status != AttendanceStatus.on_leave_requested.value:
            raise InvalidStateException(
                f"User '{membername}' does not have a pending leave request"
            )

        now = now_utc_ms()
        record.previous_status = record.status
        record.status = AttendanceStatus.on_leave.value
        record.recorded_at = now

        await self.db.flush()

        _ = await self._credit.reconcile(
            event_id, occurrence_time_utc, membername, actor
        )

        _ = await self._notifications.clear_actionable(
            "attendance_correction", pending_action_id=record.id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="attendance.correction_response",
                recipients=[membername],
                data=self._attendance_facts(
                    event_id, occurrence_time_utc, membername, outcome="approved"
                ),
            )
        )

    async def reject_leave(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        is_super_admin: bool = False,
        actor: str | None = None,
    ) -> None:
        """Reject a leave request.

        If a previous_status exists, restore it. Otherwise delete the record.

        Where the restored status is chargeable and the deployment runs on
        credits, rejecting the request charges for the session (#294, R47).
        A member who has since run out of credit cannot be restored to a
        charged status; the admin has to resolve the credit first.
        """
        await self.reject_if_cancelled(event_id, occurrence_time_utc)
        await self.check_enrollment_eligible(
            event_id, occurrence_time_utc, membername, is_super_admin
        )
        result = await self.db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        record = result.scalar_one_or_none()

        if not record:
            raise AttendanceNotFoundException(event_id, occurrence_time_utc, membername)

        if record.status != AttendanceStatus.on_leave_requested.value:
            raise InvalidStateException(
                f"User '{membername}' does not have a pending leave request"
            )

        record_id = record.id
        if record.previous_status:
            record.status = record.previous_status
            record.previous_status = AttendanceStatus.on_leave_requested.value
            record.leave_reason = None
            record.recorded_at = now_utc_ms()
        else:
            await self.db.delete(record)
        await self.db.flush()

        _ = await self._credit.reconcile(
            event_id, occurrence_time_utc, membername, actor
        )

        _ = await self._notifications.clear_actionable(
            "attendance_correction", pending_action_id=record_id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="attendance.correction_response",
                recipients=[membername],
                data=self._attendance_facts(
                    event_id, occurrence_time_utc, membername, outcome="rejected"
                ),
            )
        )
