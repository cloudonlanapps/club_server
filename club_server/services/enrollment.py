"""Enrollment service for enrollment management operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.event import Event
from ..db.models.user import User, UserStatus
from ..exceptions import (
    AlreadyEnrolledException,
    EnrollmentNotFoundException,
    EnrollmentStateConflictException,
    EnrollmentTimeConflictException,
    EventConflictException,
    EnrollmentTransitionException,
    EventNotFoundException,
    InvalidStateException,
    UserNotEligibleForEventException,
    UserNotFoundException,
)
from ..schemas.common import UserRoles
from ..schemas.enrollment import EnrollmentListResponse, EnrollmentResponse
from ..services.event_eligibility import (
    event_window,
    is_user_eligible_for_event,
    still_meets_criteria,
)
from ..schemas.credit import CreditDispositionRequest
from ..services.credit_charge import CreditChargeService
from ..services.notification import NotificationEvent, NotificationService
from ..utils import now_utc_ms
from .conflict_gates import (
    ConflictGate,
    Finding,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
)
from .lifecycle import has_live_occurrence_at_or_after, is_past


ACTIVE_ENROLLMENT_STATUSES = {
    EnrollmentStatus.accepted.value,
    EnrollmentStatus.assigned.value,
    EnrollmentStatus.assigned_trial.value,
    EnrollmentStatus.withdraw_requested.value,
}

TERMINAL_STATUSES = {
    EnrollmentStatus.declined.value,
    EnrollmentStatus.withdrawn.value,
    EnrollmentStatus.removed.value,
    EnrollmentStatus.rejected.value,
}


class EnrollmentService:
    """Service for enrollment management operations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)
        # Joining a programme requires usable credit where the deployment
        # runs on credits (#294, R35). Nothing is deducted here — credit is
        # spent by attendance, never by joining (R39).
        self._credit: CreditChargeService = CreditChargeService(db)

    async def _list_staff_usernames(self) -> list[str]:
        """Recipients for staff-facing enrollment events.

        Staff-facing enrollment events fan out to all active admins and
        coaches plus super-admins. The event's organizer and coaches are
        user references (#386), so narrowing this audience to them is
        possible; doing so is a separate change.
        """
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

    def _event_data(self, event: Event, **extra: object) -> dict[str, object]:
        """Shared fact payload fragment for enrollment events."""
        data: dict[str, object] = {
            "eventId": event.id,
            "eventTitle": event.title,
            "eventType": event.type,
        }
        data.update(extra)
        return data

    async def get_event_or_raise(self, event_id: int) -> Event:
        """Get event by ID or raise exception."""
        result = await self.db.execute(
            select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
        )
        event = result.scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)
        return event

    async def check_event_joinable(
        self, event: Event, is_super_admin: bool = False
    ) -> None:
        """Lifecycle L12: join-side flows need a live occurrence at or after now.

        One predicate for every type and every ending verb: a terminated
        programme or a cancelled camp still accepts members up to its cutoff
        (L21, programme R5), and a dropped one-off, a finished camp or a
        programme past its cutoff accept none. Super admin bypasses.
        """
        if is_super_admin:
            return
        if not await has_live_occurrence_at_or_after(self.db, event, now_utc_ms()):
            raise InvalidStateException(f"Event {event.id} has ended")

    async def check_event_not_past(
        self, event: Event, is_super_admin: bool = False
    ) -> None:
        """Raise if the event has ended (L7); super admin bypasses.

        Exit-side flows use this so that a decision, a withdrawal or a
        removal on an event that has finished is a retrospective correction
        only a super admin makes.
        """
        if is_super_admin:
            return
        if await is_past(self.db, event, now_utc_ms()):
            raise InvalidStateException(f"Event {event.id} has ended")

    async def get_user_or_raise(self, membername: str) -> User:
        """Get user by username or raise exception."""
        result = await self.db.execute(
            select(User).where(User.username == membername, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(membername)
        return user

    async def check_event_eligibility(self, user: User, event: Event) -> None:
        """Raise if `user` does not satisfy the event's structured eligibility criteria.

        The age band is checked as the window it comes to today, on the
        event's reference day (eligibility R13).

        Mirrors the rule introduced for groups in #11. No staff
        exemption; no super-admin override (eligibility is a data-safety
        invariant, not a temporal one).
        """
        window = await event_window(self.db, event)
        if not is_user_eligible_for_event(user, event, window):
            raise UserNotEligibleForEventException(user.username, event.id)

    async def get_enrollment_or_raise(
        self, event_id: int, membername: str
    ) -> Enrollment:
        """Get enrollment or raise exception."""
        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == membername
            )
        )
        enrollment = result.scalar_one_or_none()
        if not enrollment:
            raise EnrollmentNotFoundException(event_id, membername)
        return enrollment

    async def check_member_gate(self, event: Event, membername: str) -> list[Finding]:
        """The member gate: is this person enrolled in two things at once?

        Blocks only for a programme-against-programme clash (enrollment R30,
        camp R39, one-off R20); every other pairing is reported and the
        findings returned. Overlap is per occurrence (R29a), and a candidate
        counts while it still has live occurrences (R29b).
        """
        current = event.current_schedule
        try:
            report = await check_conflicts(
                self.db,
                ScheduleTarget(
                    event_type=event.type,
                    venue_id=current.venue_id,
                    start_time=current.start_time,
                    end_time=current.end_time,
                    rrule=current.rrule,
                    effective_from=current.effective_from,
                    effective_until=current.effective_until,
                    membername=membername,
                    exclude_event_id=event.id,
                ),
                {ConflictGate.member: GatePolicy.block},
            )
        except EventConflictException as e:
            raise EnrollmentTimeConflictException(
                membername=membername,
                target_event_id=event.id,
                conflicting_event_ids=[f.event_id for f in e.findings],
            ) from e
        return report.findings

    def _begin_stint(
        self,
        enrollment: Enrollment,
        status: EnrollmentStatus,
        now: int,
        *,
        is_trial: bool = False,
    ) -> None:
        """Move ``enrollment`` into an enrolled status as a fresh stint.

        A rejoin reuses the member's row, so the previous departure is
        cleared here on every path — assign, assign-trial, accepting an
        invitation and approving a request (#465). Left in place, the old
        ``withdrawn_at`` would end the new stint before it began.

        The trial flag is set from the path taken, never inherited: a former
        trial member who rejoins as a paying one spends paid credit (#468,
        credit R53).

        A departure whose credit settlement is still deferred (programme
        R29c) is not yet over, so no path may rejoin until the sweep has
        applied it (#469).
        """
        if enrollment.pending_disposition is not None:
            raise EnrollmentStateConflictException(
                f"User '{enrollment.membername}' has a departure settlement"
                " pending; rejoin once it has been applied"
            )
        enrollment.previous_status = enrollment.status
        enrollment.status = status.value
        enrollment.is_trial = 1 if is_trial else 0
        enrollment.enrolled_at = now
        enrollment.withdrawal_reason = None
        enrollment.withdrawn_at = None
        enrollment.updated_at = now

    async def list_enrollments(
        self,
        event_id: int,
        status_filter: str | None = None,
    ) -> EnrollmentListResponse:
        """List enrollments for an event."""
        event = await self.get_event_or_raise(event_id)

        query = select(Enrollment).where(Enrollment.event_id == event_id)
        if status_filter:
            query = query.where(Enrollment.status == status_filter)

        result = await self.db.execute(query)
        enrollments = result.scalars().all()

        window = await event_window(self.db, event)
        return EnrollmentListResponse(
            enrollments={e.membername: e.status for e in enrollments},
            records=[
                EnrollmentResponse.from_model(
                    e, still_meets_criteria(e, e.user, event, window)
                )
                for e in enrollments
            ],
        )

    async def enrollment_response(self, enrollment: Enrollment) -> EnrollmentResponse:
        """One enrolment as the API returns it, with ``eligible`` worked out (R20)."""
        event = enrollment.event
        window = await event_window(self.db, event)
        return EnrollmentResponse.from_model(
            enrollment, still_meets_criteria(enrollment, enrollment.user, event, window)
        )

    async def get_enrollment_status(self, event_id: int, membername: str) -> str | None:
        """Get enrollment status for a user."""
        _ = await self.get_event_or_raise(event_id)

        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == membername
            )
        )
        enrollment = result.scalar_one_or_none()
        return enrollment.status if enrollment else None

    async def invite_user(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Invite a user to an event.

        Not credit-gated (#446, R35a): an invitation is an offer, and goes out
        before payment comes in. The check sits at accept.
        """
        user = await self.get_user_or_raise(membername)
        event = await self.get_event_or_raise(event_id)
        await self.check_event_joinable(event, is_super_admin)
        await self.check_event_eligibility(user, event)

        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == membername
            )
        )
        existing = result.scalar_one_or_none()

        now = now_utc_ms()
        if existing:
            if existing.status not in TERMINAL_STATUSES:
                raise AlreadyEnrolledException(membername)
            existing.previous_status = existing.status
            existing.status = EnrollmentStatus.invited.value
            existing.withdrawal_reason = None
            existing.updated_at = now
            enrollment = existing
        else:
            enrollment = Enrollment(
                event_id=event_id,
                membername=membername,
                status=EnrollmentStatus.invited.value,
                created_at=now,
                updated_at=now,
            )
            self.db.add(enrollment)

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.opened",
                recipients=[membername],
                data=self._event_data(event),
                pending_action_type="enrollment_opportunity",
                pending_action_id=enrollment.id,
            )
        )

    async def assign_user(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Assign a user to an event directly."""
        user = await self.get_user_or_raise(membername)
        event = await self.get_event_or_raise(event_id)
        await self.check_event_joinable(event, is_super_admin)
        await self.check_event_eligibility(user, event)
        await self._credit.ensure_enrollment_credit(event, membername, is_trial=False)
        _ = await self.check_member_gate(event, membername)

        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == membername
            )
        )
        existing = result.scalar_one_or_none()

        now = now_utc_ms()
        if existing:
            if existing.status in ACTIVE_ENROLLMENT_STATUSES:
                raise AlreadyEnrolledException(membername)
            self._begin_stint(existing, EnrollmentStatus.assigned, now)
        else:
            enrollment = Enrollment(
                event_id=event_id,
                membername=membername,
                status=EnrollmentStatus.assigned.value,
                enrolled_at=now,
                created_at=now,
                updated_at=now,
            )
            self.db.add(enrollment)

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.admin_enrolled",
                recipients=[membername],
                data=self._event_data(event, trial=False),
            )
        )

    async def assign_trial(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Assign a trial to a user."""
        user = await self.get_user_or_raise(membername)
        event = await self.get_event_or_raise(event_id)
        await self.check_event_joinable(event, is_super_admin)
        await self.check_event_eligibility(user, event)
        await self._credit.ensure_enrollment_credit(event, membername, is_trial=True)
        _ = await self.check_member_gate(event, membername)

        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == membername
            )
        )
        existing = result.scalar_one_or_none()

        now = now_utc_ms()
        if existing:
            if existing.status in ACTIVE_ENROLLMENT_STATUSES:
                raise AlreadyEnrolledException(membername)
            self._begin_stint(
                existing, EnrollmentStatus.assigned_trial, now, is_trial=True
            )
        else:
            enrollment = Enrollment(
                event_id=event_id,
                membername=membername,
                status=EnrollmentStatus.assigned_trial.value,
                is_trial=1,
                enrolled_at=now,
                created_at=now,
                updated_at=now,
            )
            self.db.add(enrollment)

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.admin_enrolled",
                recipients=[membername],
                data=self._event_data(event, trial=True),
            )
        )

    async def approve_request(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Approve an enrollment request.

        Re-runs eligibility at approval time. If the requester is no
        longer eligible (e.g., the event's criteria were tightened
        between request and decision), raises
        ``UserNotEligibleForEventException``; the enrollment remains in
        ``requested`` status. Approval is where the member becomes enrolled,
        so it runs the member clash gate as assign and accept do (#466,
        R30): a request does not count against another request.
        """
        event = await self.get_event_or_raise(event_id)
        await self.check_event_joinable(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.requested.value:
            raise EnrollmentTransitionException("approve", enrollment.status)

        user = await self.get_user_or_raise(membername)
        await self.check_event_eligibility(user, event)
        await self._credit.ensure_enrollment_credit(event, membername, is_trial=False)
        _ = await self.check_member_gate(event, membername)

        self._begin_stint(enrollment, EnrollmentStatus.accepted, now_utc_ms())

        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "enrollment_request", pending_action_id=enrollment.id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.admin_enrolled",
                recipients=[membername],
                data=self._event_data(event, trial=False, viaRequest=True),
            )
        )

    async def reject_request(
        self,
        event_id: int,
        membername: str,
        reason: str | None = None,
        is_super_admin: bool = False,
    ) -> None:
        """Reject an enrollment request."""
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.requested.value:
            raise EnrollmentTransitionException("reject", enrollment.status)

        now = now_utc_ms()
        enrollment.previous_status = enrollment.status
        enrollment.status = EnrollmentStatus.rejected.value
        enrollment.withdrawal_reason = reason
        enrollment.updated_at = now

        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "enrollment_request", pending_action_id=enrollment.id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.closed",
                recipients=[membername],
                data=self._event_data(event, reason=reason),
            )
        )

    async def remove_enrollment(
        self,
        event_id: int,
        membername: str,
        reason: str | None = None,
        is_super_admin: bool = False,
        credit_disposition: CreditDispositionRequest | None = None,
        actor: str | None = None,
    ) -> None:
        """Remove an enrollment from event.

        Where the deployment runs on credits and the member holds a balance
        bound to this programme, the admin must say what happens to it
        (#294, R71). The credit is settled before the enrollment changes, so
        a missing disposition leaves the member enrolled rather than removed
        with their credit stranded.

        A member who has already left cannot be removed again (#467): it
        would re-date their departure to now, covering every session since
        they actually left, and settle it a second time.
        """
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)
        if enrollment.status in TERMINAL_STATUSES:
            raise EnrollmentStateConflictException(
                f"Cannot remove enrollment with status '{enrollment.status}'"
            )
        await self._credit.settle_departure(
            event, enrollment, credit_disposition, actor
        )

        now = now_utc_ms()
        enrollment.previous_status = enrollment.status
        enrollment.status = EnrollmentStatus.removed.value
        enrollment.withdrawal_reason = reason
        enrollment.withdrawn_at = now
        enrollment.updated_at = now

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.cancelled_admin",
                recipients=[membername],
                data=self._event_data(event, reason=reason),
            )
        )

    async def approve_withdrawal(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
        credit_disposition: CreditDispositionRequest | None = None,
        actor: str | None = None,
    ) -> None:
        """Approve a withdrawal request.

        Approving carries the same credit disposition as a removal (#294,
        R73): a member who walks away from a programme still has to have
        their bound balance resolved. Rejecting a withdrawal leaves every
        account untouched.

        A withdrawal request carries no actionable notice, so deciding it
        deletes none: staff keep the enrollment's request notice (#515).
        """
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.withdraw_requested.value:
            raise EnrollmentTransitionException("approve withdrawal", enrollment.status)

        await self._credit.settle_departure(
            event, enrollment, credit_disposition, actor
        )

        now = now_utc_ms()
        enrollment.previous_status = enrollment.status
        enrollment.status = EnrollmentStatus.withdrawn.value
        enrollment.withdrawn_at = now
        enrollment.updated_at = now

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.cancelled_admin",
                recipients=[membername],
                data=self._event_data(event, viaWithdrawal=True),
            )
        )

    async def reject_withdrawal(
        self,
        event_id: int,
        membername: str,
        reason: str | None = None,
        is_super_admin: bool = False,
    ) -> None:
        """Reject a withdrawal request. Restores previous_status.

        Deletes no notice: staff keep the enrollment's request notice (#515).
        """
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.withdraw_requested.value:
            raise EnrollmentTransitionException("reject withdrawal", enrollment.status)

        now = now_utc_ms()
        restored_status = enrollment.previous_status or EnrollmentStatus.accepted.value
        enrollment.previous_status = enrollment.status
        enrollment.status = restored_status
        enrollment.withdrawal_reason = None
        enrollment.updated_at = now

        await self.db.flush()

    async def accept_invite(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Accept an invitation."""
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.invited.value:
            raise EnrollmentTransitionException("accept invitation", enrollment.status)

        event = await self.get_event_or_raise(event_id)
        await self.check_event_joinable(event, is_super_admin)
        _ = await self.check_member_gate(event, membername)
        await self._credit.ensure_enrollment_credit(event, membername, is_trial=False)

        self._begin_stint(enrollment, EnrollmentStatus.accepted, now_utc_ms())

        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "enrollment_opportunity", pending_action_id=enrollment.id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.rsvp",
                recipients=await self._list_staff_usernames(),
                data=self._event_data(
                    event, memberUsername=membername, outcome="accepted"
                ),
            )
        )

    async def decline_invite(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Decline an invitation."""
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.invited.value:
            raise EnrollmentTransitionException("decline invitation", enrollment.status)

        enrollment.previous_status = enrollment.status
        enrollment.status = EnrollmentStatus.declined.value
        enrollment.updated_at = now_utc_ms()

        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "enrollment_opportunity", pending_action_id=enrollment.id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.rsvp",
                recipients=await self._list_staff_usernames(),
                data=self._event_data(
                    event, memberUsername=membername, outcome="declined"
                ),
            )
        )

    async def request_enrollment(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Request to join an event."""
        event = await self.get_event_or_raise(event_id)
        await self.check_event_joinable(event, is_super_admin)
        user = await self.get_user_or_raise(membername)
        await self.check_event_eligibility(user, event)
        await self._credit.ensure_enrollment_credit(event, membername, is_trial=False)
        _ = await self.check_member_gate(event, membername)

        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == membername
            )
        )
        existing = result.scalar_one_or_none()

        now = now_utc_ms()
        if existing:
            if existing.status not in TERMINAL_STATUSES:
                raise AlreadyEnrolledException(membername)
            existing.previous_status = existing.status
            existing.status = EnrollmentStatus.requested.value
            existing.withdrawal_reason = None
            existing.updated_at = now
            enrollment = existing
        else:
            enrollment = Enrollment(
                event_id=event_id,
                membername=membername,
                status=EnrollmentStatus.requested.value,
                created_at=now,
                updated_at=now,
            )
            self.db.add(enrollment)

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.rsvp",
                recipients=await self._list_staff_usernames(),
                data=self._event_data(
                    event, memberUsername=membername, outcome="requested"
                ),
                pending_action_type="enrollment_request",
                pending_action_id=enrollment.id,
            )
        )

    async def request_withdrawal(
        self,
        event_id: int,
        membername: str,
        reason: str | None = None,
        is_super_admin: bool = False,
    ) -> None:
        """Request to withdraw from an event."""
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        allowed_statuses = {
            EnrollmentStatus.accepted.value,
            EnrollmentStatus.assigned.value,
            EnrollmentStatus.assigned_trial.value,
        }
        if enrollment.status not in allowed_statuses:
            raise EnrollmentTransitionException("request withdrawal", enrollment.status)

        now = now_utc_ms()
        enrollment.previous_status = enrollment.status
        enrollment.status = EnrollmentStatus.withdraw_requested.value
        enrollment.withdrawal_reason = reason
        enrollment.updated_at = now

        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.cancelled_self",
                recipients=await self._list_staff_usernames(),
                data=self._event_data(event, memberUsername=membername, reason=reason),
            )
        )

    async def cancel_withdrawal(
        self,
        event_id: int,
        membername: str,
        is_super_admin: bool = False,
    ) -> None:
        """Cancel a withdrawal request. Restores previous_status."""
        event = await self.get_event_or_raise(event_id)
        await self.check_event_not_past(event, is_super_admin)
        enrollment = await self.get_enrollment_or_raise(event_id, membername)

        if enrollment.status != EnrollmentStatus.withdraw_requested.value:
            raise EnrollmentTransitionException("cancel withdrawal", enrollment.status)

        restored_status = enrollment.previous_status or EnrollmentStatus.accepted.value
        enrollment.previous_status = enrollment.status
        enrollment.status = restored_status
        enrollment.withdrawal_reason = None
        enrollment.updated_at = now_utc_ms()

        await self.db.flush()
