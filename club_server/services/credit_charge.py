"""Charging and refunding sessions (#294).

Charging is expressed as a *reconciliation*, not as a set of
per-transition actions (R44a): an occurrence is charged if and only if the
member's resulting attendance status is present, absent or late. Every
attendance transition calls the same function, which compares that rule to
what the ledger already says and moves credit only where they differ.

That is what makes present->absent free, a double charge unreachable, and
"cancelled occurrence behaves like approved leave" fall out rather than
need its own branch (R46-R48). It is also idempotent (R44b), so a retried
attendance write cannot charge twice.

Every entry point is inert where the deployment does not run on credits
(R95): enrollment and attendance then behave exactly as they did before
this subsystem existed.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.attendance import AttendanceRecord, AttendanceStatus
from ..db.models.credit_entry import CreditEntry, CreditEntryType
from ..db.models.credit_session_charge import CreditSessionCharge
from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.event import Event
from ..schemas.credit import CreditDispositionRequest as CreditDisposition
from ..exceptions import (
    CreditDispositionNotApplicableException,
    CreditDispositionRequiredException,
    InsufficientCreditException,
)
from ..utils import now_utc_ms
from .audit import AuditService
from .audit_actions import SYSTEM_ACTOR, AuditAction
from .credit import CreditService
from .credit_lifecycle import CreditLifecycleService
from .credit_pending import pending_disposition_json
from .credit_selection import CreditSelection, session_cost_for
from .event_types import is_programme
from .lifecycle import is_occurrence_cancelled
from .notification import NotificationEvent, NotificationService
from .schedule import duration_of, event_slots

# The statuses that consume a credit. Everything else — both leave statuses,
# and the absence of a record — is uncharged (R44a, R45).
# ``withdrawal_reason`` on an enrollment ended by its trial running out
# (R52): a fixed code, so the member's app can tell it from an admin removal.
TRIAL_CREDIT_EXHAUSTED = "trialCreditExhausted"

CHARGEABLE_STATUSES = {
    AttendanceStatus.present.value,
    AttendanceStatus.absent.value,
    AttendanceStatus.late.value,
}


class CreditChargeService:
    """Keeps the ledger in step with attendance."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._credits: CreditService = CreditService(db)
        self._lifecycle: CreditLifecycleService = CreditLifecycleService(db)
        # Money-adjacent movements are auditable, not only visible on the
        # ledger (R84). The ledger says what the balance is; the audit log
        # is where someone goes to ask what happened.
        self._audit: AuditService = AuditService(db)
        self._selection: CreditSelection = CreditSelection(db)
        self._notifications: NotificationService = NotificationService(db)

    @property
    def active(self) -> bool:
        """Whether this deployment runs on credits at all (R92, R95)."""
        return settings.credit_system_enabled

    # --- gates ---------------------------------------------------------

    async def ensure_enrollment_credit(
        self, event: Event, membername: str, *, is_trial: bool | None = None
    ) -> None:
        """Refuse an enrollment action from a member with no usable credit.

        The threshold is one credit, not the cost of the whole programme
        (R36): a member with a single credit may join, and is stopped at
        the first session they cannot pay for. Nothing is deducted here —
        credit is spent by attendance, never by joining (R39).

        ``is_trial`` must be passed by callers that are *about* to make the
        enrollment a trial: the flag is not on the row yet at that point, so
        deriving it would check ordinary accounts and let a member onto a
        trial they cannot fund (R53).
        """
        if not self.active or not is_programme(event):
            return
        root_id = event.id
        if is_trial is None:
            is_trial = await self._is_trial_enrollment(event.id, membername)
        available = await self._selection.usable_credits(
            membername, root_id, is_trial=is_trial
        )
        if available < 1:
            raise InsufficientCreditException(membername, event.id, 1)

    async def ensure_can_charge(
        self, event: Event, occurrence_time_utc: int, membername: str
    ) -> None:
        """Refuse an attendance mark the member cannot pay for (R41, R41a).

        Called *before* the attendance record is written so that a refusal
        leaves no record and no partial state.
        """
        if not self.active or not is_programme(event):
            return
        if await self._active_charge(event.id, occurrence_time_utc, membername):
            # Already paid for; a status change within the chargeable set
            # costs nothing further (R46).
            return
        root_id = event.id
        is_trial = await self._is_trial_enrollment(event.id, membername)
        cost = session_cost_for(event)
        allocation = await self._selection.allocate(
            membername, root_id, cost, is_trial=is_trial
        )
        if allocation is None:
            raise InsufficientCreditException(membername, event.id, cost)

    async def settle_departure(
        self,
        event: Event,
        enrollment: Enrollment,
        disposition: "CreditDisposition | None",
        actor: str | None,
    ) -> None:
        """Resolve programme credit when a member leaves (R71, R73, R74a).

        The admin states the disposition as part of the departure; a member
        holding bound credit cannot leave without one. Where an occurrence
        the member is still enrolled for is under way, the settlement is
        **deferred** to its end rather than taken at the call (programme
        R29c–R29e): the balance stays bound and spends normally until then,
        and the sweep applies the disposition to whatever is left.
        """
        if not self.active or not is_programme(event):
            if disposition is not None:
                raise CreditDispositionNotApplicableException()
            return

        balance = await self._credits.bound_balance(enrollment.membername, event.id)
        if balance <= 0:
            return
        if disposition is None:
            raise CreditDispositionRequiredException(
                enrollment.membername, event.id, balance
            )

        settle_after = await self._covered_occurrence_end(event, enrollment)
        if settle_after is not None:
            enrollment.pending_disposition = pending_disposition_json(
                disposition, actor
            )
            enrollment.settle_after_utc = settle_after
            return

        _ = await self._lifecycle.dispose_bound_credit(
            membername=enrollment.membername,
            root_event_id=event.id,
            penalty=disposition.penalty,
            valid_from=disposition.valid_from_utc,
            valid_until=disposition.valid_until_utc,
            reason=disposition.reason,
            actor=actor or enrollment.membername,
        )

    async def _covered_occurrence_end(
        self, event: Event, enrollment: Enrollment
    ) -> int | None:
        """The end of an occurrence under way that the enrollment covers, if any.

        A departing member stays chargeable for an occurrence that has
        started but not finished (R74); only once it has passed is the amount
        they were actually charged known.
        """
        now = now_utc_ms()
        longest = max(duration_of(s) for s in event.schedules)
        latest_end: int | None = None
        for slot in event_slots(event, now - longest, now + 1):
            if slot.time > now or slot.end <= now:
                continue
            if (
                enrollment.enrolled_at is not None
                and enrollment.enrolled_at > slot.time
            ):
                continue
            if await is_occurrence_cancelled(self.db, event, slot.time):
                continue
            latest_end = slot.end if latest_end is None else max(latest_end, slot.end)
        return latest_end

    # --- reconciliation ------------------------------------------------

    async def reconcile(
        self,
        event_id: int,
        occurrence_time_utc: int,
        membername: str,
        actor: str | None,
    ) -> bool:
        """Bring the ledger into line with the attendance record (R44a).

        The single entry point used by every attendance transition. Charges
        where the resulting status is chargeable and nothing has been paid,
        refunds where it is not and something has, and does nothing at all
        when the two already agree.

        Returns True when the charge spent the last of a trial and so ended
        the member's enrollment (R52), so the caller can say so.
        """
        if not self.active:
            return False
        event = await self.db.get(Event, event_id)
        if event is None or not is_programme(event):
            return False

        status = await self._attendance_status(
            event_id, occurrence_time_utc, membername
        )
        # A cancelled session is uncharged whatever the record still says
        # (R48). Expressing it here rather than as a branch in the cancel
        # path keeps the whole rule in one place: cancelling then calls
        # reconcile like every other transition, and gets a refund.
        cancelled = await is_occurrence_cancelled(self.db, event, occurrence_time_utc)
        should_charge = status in CHARGEABLE_STATUSES and not cancelled
        charged = await self._active_charge(event_id, occurrence_time_utc, membername)

        if should_charge and charged is None:
            return await self._charge(event, occurrence_time_utc, membername, actor)
        if not should_charge and charged is not None:
            await self._refund(charged, actor)
        return False

    async def _charge(
        self,
        event: Event,
        occurrence_time_utc: int,
        membername: str,
        actor: str | None,
    ) -> bool:
        """Take the session's cost, splitting across accounts if needed (R29).

        Returns True when the charge ended the member's trial.
        """
        root_id = event.id
        is_trial = await self._is_trial_enrollment(event.id, membername)
        cost = session_cost_for(event)
        allocation = await self._selection.allocate(
            membername, root_id, cost, is_trial=is_trial
        )
        if allocation is None:
            raise InsufficientCreditException(membername, event.id, cost)

        charge = await self._charge_row(event.id, occurrence_time_utc, membername)
        charge.total = cost
        charge.refunded_at = None
        await self.db.flush()

        for part in allocation:
            entry = CreditEntry(
                account_id=part.account.id,
                amount=-part.amount,
                entry_type=CreditEntryType.session_deduction.value,
                event_id=root_id,
                occurrence_time_utc=occurrence_time_utc,
                charge_id=charge.id,
                reason=f"Session on {occurrence_time_utc}",
                actor_username=actor,
                created_at=now_utc_ms(),
            )
            self.db.add(entry)
        await self.db.flush()

        await self._audit.log(
            actor_username=actor or membername,
            action=AuditAction.CREDIT_DEDUCTED,
            target_username=membername,
            resource_type="occurrence",
            resource_id=f"{event.id}:{occurrence_time_utc}",
            details={"credits": cost, "eventId": root_id},
        )

        if is_trial:
            return await self._remove_if_trial_exhausted(
                event, occurrence_time_utc, membername, actor
            )
        return False

    async def _refund(self, charge: CreditSessionCharge, actor: str | None) -> None:
        """Return the cost to the exact accounts that paid it (R49)."""
        deductions = await self._unrefunded_deductions(charge.id)
        paying = [e.account_id for e in deductions]
        await self._credits.lock_accounts(paying)
        await self._lifecycle.reopen_for_refund(
            paying,
            event_id=charge.event_id,
            occurrence_time_utc=charge.occurrence_time_utc,
            actor=actor,
        )
        for entry in deductions:
            self.db.add(
                CreditEntry(
                    account_id=entry.account_id,
                    amount=-entry.amount,
                    entry_type=CreditEntryType.session_refund.value,
                    event_id=entry.event_id,
                    occurrence_time_utc=entry.occurrence_time_utc,
                    charge_id=charge.id,
                    reason="Attendance reversed",
                    actor_username=actor,
                    created_at=now_utc_ms(),
                    offsets_entry_id=entry.id,
                )
            )
        charge.refunded_at = now_utc_ms()
        await self.db.flush()

        await self._audit.log(
            actor_username=actor or charge.membername,
            action=AuditAction.CREDIT_REFUNDED,
            target_username=charge.membername,
            resource_type="occurrence",
            resource_id=f"{charge.event_id}:{charge.occurrence_time_utc}",
            details={"credits": charge.total, "eventId": charge.event_id},
        )

    # --- helpers -------------------------------------------------------

    async def _attendance_status(
        self, event_id: int, occurrence_time_utc: int, membername: str
    ) -> str | None:
        result = await self.db.execute(
            select(AttendanceRecord.status).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
                AttendanceRecord.membername == membername,
            )
        )
        return result.scalar_one_or_none()

    async def reconcile_occurrence(
        self, event_id: int, occurrence_time_utc: int, actor: str | None
    ) -> None:
        """Reconcile every member marked at one occurrence.

        Used when something changes about the occurrence itself rather than
        about one member — cancelling it, for instance (R48).
        """
        if not self.active:
            return
        result = await self.db.execute(
            select(AttendanceRecord.membername).where(
                AttendanceRecord.event_id == event_id,
                AttendanceRecord.occurrence_time_utc == occurrence_time_utc,
            )
        )
        for membername in list(result.scalars().all()):
            _ = await self.reconcile(event_id, occurrence_time_utc, membername, actor)

    async def _active_charge(
        self, event_id: int, occurrence_time_utc: int, membername: str
    ) -> CreditSessionCharge | None:
        result = await self.db.execute(
            select(CreditSessionCharge).where(
                CreditSessionCharge.event_id == event_id,
                CreditSessionCharge.occurrence_time_utc == occurrence_time_utc,
                CreditSessionCharge.membername == membername,
                CreditSessionCharge.refunded_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _charge_row(
        self, event_id: int, occurrence_time_utc: int, membername: str
    ) -> CreditSessionCharge:
        """Fetch or create the charge row for this member and occurrence.

        The row is reused across refund and re-charge rather than replaced,
        because its unique constraint — the same one attendance_records
        carries — is what makes a double charge impossible (R83).
        """
        result = await self.db.execute(
            select(CreditSessionCharge).where(
                CreditSessionCharge.event_id == event_id,
                CreditSessionCharge.occurrence_time_utc == occurrence_time_utc,
                CreditSessionCharge.membername == membername,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        charge = CreditSessionCharge(
            event_id=event_id,
            occurrence_time_utc=occurrence_time_utc,
            membername=membername,
            total=0,
            created_at=now_utc_ms(),
        )
        self.db.add(charge)
        await self.db.flush()
        return charge

    async def _unrefunded_deductions(self, charge_id: int) -> list[CreditEntry]:
        """Deductions on this charge that no refund entry offsets yet."""
        offset_ids = select(CreditEntry.offsets_entry_id).where(
            CreditEntry.offsets_entry_id.is_not(None)
        )
        result = await self.db.execute(
            select(CreditEntry).where(
                CreditEntry.charge_id == charge_id,
                CreditEntry.entry_type == CreditEntryType.session_deduction.value,
                CreditEntry.id.not_in(offset_ids),
            )
        )
        return list(result.scalars().all())

    async def _is_trial_enrollment(self, event_id: int, membername: str) -> bool:
        result = await self.db.execute(
            select(Enrollment.is_trial).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == membername,
            )
        )
        return bool(result.scalar_one_or_none())

    async def _remove_if_trial_exhausted(
        self,
        event: Event,
        occurrence_time_utc: int,
        membername: str,
        actor: str | None,
    ) -> bool:
        """Remove a trial member once their trial credit is spent (R52).

        The only case in which running out of credit ends an enrollment
        rather than merely blocking attendance: a completed trial is over,
        not paused waiting for a top-up. It is a departure like any other, so
        ``withdrawn_at`` bounds the stint the member's coverage of past
        occurrences is judged by. The removal is the system's (R52a); the
        audit row names the coach whose mark triggered it (R52b, R82).

        Returns True when the enrollment was removed.
        """
        remaining = await self._selection.usable_credits(
            membername, event.id, is_trial=True
        )
        if remaining > 0:
            return False
        result = await self.db.execute(
            select(Enrollment).where(
                Enrollment.event_id == event.id,
                Enrollment.membername == membername,
            )
        )
        enrollment = result.scalar_one_or_none()
        if enrollment is None:
            return False
        now = now_utc_ms()
        enrollment.previous_status = enrollment.status
        enrollment.status = EnrollmentStatus.removed.value
        enrollment.withdrawal_reason = TRIAL_CREDIT_EXHAUSTED
        enrollment.withdrawn_at = now
        enrollment.updated_at = now
        await self.db.flush()

        await self._audit.log(
            actor_username=SYSTEM_ACTOR,
            action=AuditAction.ENROLLMENT_REMOVED,
            target_username=membername,
            resource_type="event",
            resource_id=str(event.id),
            details={
                "reason": TRIAL_CREDIT_EXHAUSTED,
                "triggeredBy": actor,
                "occurrenceTimeUtc": occurrence_time_utc,
            },
        )
        _ = await self._notifications.notify_for_event(
            NotificationEvent(
                type="enrollment.trial_ended",
                recipients=[membername],
                data={
                    "eventId": event.id,
                    "eventTitle": event.title,
                    "eventType": event.type,
                    "enrolledAtUtc": enrollment.enrolled_at,
                    "withdrawnAtUtc": now,
                },
            )
        )
        return True
