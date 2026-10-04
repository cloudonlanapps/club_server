"""Evaluation service (#302, #535).

An evaluation exists, before publication, only for its effective owner
(R38a): every staff-side read and write goes through ``get_owned_or_raise``,
which answers not-found to anyone else.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation, EvaluationStatus
from ..db.models.evaluation_template import EvaluationTemplate
from ..db.models.user import User
from ..exceptions import (
    EvaluationIncompleteException,
    EvaluationNotFoundException,
    EvaluationTransitionException,
    HardDeleteNeedsSoftDeleteException,
    InvalidStateException,
    NothingToRestoreException,
    UserNotFoundException,
)
from ..schemas.evaluation import PERIOD_FIELDS, EvaluationCreate, EvaluationUpdate
from ..utils import now_utc_ms
from .evaluation_answers import incomplete_items
from .evaluation_constraints import refuse_duplicate, refuse_future_period
from .evaluation_eligibility import EvaluationEligibilityService
from .evaluation_member_copy import retire_member_copies
from .evaluation_template import EvaluationTemplateService


class EvaluationService:
    """Ownership, lifecycle and listing for evaluations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self.templates: EvaluationTemplateService = EvaluationTemplateService(db)
        self.eligibility: EvaluationEligibilityService = EvaluationEligibilityService(
            db
        )

    async def get_user_or_raise(self, username: str) -> User:
        """Fetch a live user or raise."""
        result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)
        return user

    async def get_or_raise(
        self, evaluation_id: int, include_deleted: bool = False
    ) -> Evaluation:
        """Fetch an evaluation or raise, whoever owns it."""
        query = select(Evaluation).where(Evaluation.id == evaluation_id)
        if not include_deleted:
            query = query.where(Evaluation.deleted_at.is_(None))
        result = await self.db.execute(query)
        evaluation = result.scalar_one_or_none()
        if not evaluation:
            raise EvaluationNotFoundException(evaluation_id)
        return evaluation

    async def get_owned_or_raise(
        self, evaluation_id: int, username: str, include_deleted: bool = False
    ) -> Evaluation:
        """Fetch an evaluation its caller owns; for anyone else it does not exist."""
        evaluation = await self.get_or_raise(evaluation_id, include_deleted)
        if evaluation.effective_owner != username:
            raise EvaluationNotFoundException(evaluation_id)
        return evaluation

    @staticmethod
    def require_draft(evaluation: Evaluation) -> None:
        """Only a draft is edited (R21, R22)."""
        if evaluation.status != EvaluationStatus.draft.value:
            raise InvalidStateException(
                "Only a draft can be edited. Revert a saved evaluation to draft; "
                "withdraw a published one first."
            )

    async def create_evaluation(
        self, payload: EvaluationCreate, coach_username: str
    ) -> Evaluation:
        """Create a draft with no answers, created by and owned by the caller (R50)."""
        _ = await self.get_user_or_raise(payload.created_for)
        _ = await self.templates.get_or_raise(payload.template_id)
        refuse_future_period(payload.period_end_utc)
        await self.eligibility.check(
            coach_username,
            payload.created_for,
            payload.event_id,
            payload.period_start_utc,
            payload.period_end_utc,
        )
        await refuse_duplicate(
            self.db,
            owner=coach_username,
            created_for=payload.created_for,
            template_id=payload.template_id,
            period_start_utc=payload.period_start_utc,
            period_end_utc=payload.period_end_utc,
        )
        now = now_utc_ms()
        evaluation = Evaluation(
            template_id=payload.template_id,
            created_for=payload.created_for,
            created_by=coach_username,
            event_id=payload.event_id,
            period_start_utc=payload.period_start_utc,
            period_end_utc=payload.period_end_utc,
            status=EvaluationStatus.draft.value,
            created_at=now,
            updated_at=now,
        )
        self.db.add(evaluation)
        await self.db.flush()
        await self.db.refresh(evaluation)
        return evaluation

    async def update_draft(
        self, evaluation: Evaluation, payload: EvaluationUpdate
    ) -> Evaluation:
        """Change a draft's event, its period, or both (R21, R23).

        A field the request omits keeps its value; ``eventId: null`` makes
        the draft general. Eligibility is checked again for the effective
        owner against the resulting event and period (R28-R33), and the
        result may not be a twin of another of the owner's reviews (R7).
        """
        self.require_draft(evaluation)
        fields = payload.model_fields_set
        event_id = payload.event_id if "event_id" in fields else evaluation.event_id
        start, end = evaluation.period_start_utc, evaluation.period_end_utc
        if fields & PERIOD_FIELDS:
            start, end = payload.period_start_utc, payload.period_end_utc
            refuse_future_period(end)
        await self.eligibility.check(
            evaluation.effective_owner, evaluation.created_for, event_id, start, end
        )
        await refuse_duplicate(
            self.db,
            owner=evaluation.effective_owner,
            created_for=evaluation.created_for,
            template_id=evaluation.template_id,
            period_start_utc=start,
            period_end_utc=end,
            own_id=evaluation.id,
        )
        evaluation.event_id = event_id
        evaluation.period_start_utc = start
        evaluation.period_end_utc = end
        evaluation.updated_at = now_utc_ms()
        await self.db.flush()
        return evaluation

    async def _refuse_twin(self, evaluation: Evaluation, owner: str) -> None:
        """``owner`` may not hold another live review of this key (R7)."""
        await refuse_duplicate(
            self.db,
            owner=owner,
            created_for=evaluation.created_for,
            template_id=evaluation.template_id,
            period_start_utc=evaluation.period_start_utc,
            period_end_utc=evaluation.period_end_utc,
            own_id=evaluation.id,
        )

    async def _transition(
        self,
        evaluation: Evaluation,
        expected: EvaluationStatus,
        target: EvaluationStatus,
        action: str,
    ) -> Evaluation:
        """The four permitted lifecycle moves, and nothing else (R19)."""
        if evaluation.status != expected.value:
            raise EvaluationTransitionException(evaluation.status, action)

        now = now_utc_ms()
        evaluation.status = target.value
        evaluation.updated_at = now
        if target == EvaluationStatus.published:
            evaluation.published_at = now
        elif expected == EvaluationStatus.published:
            # Cleared on withdrawal so the field means "currently published" (R20).
            evaluation.published_at = None
        await self.db.flush()
        return evaluation

    async def save_evaluation(self, evaluation: Evaluation) -> Evaluation:
        """Draft to saved, once every required answer and note is there (R15)."""
        if evaluation.status == EvaluationStatus.draft.value:
            template = await self.db.get(EvaluationTemplate, evaluation.template_id)
            await self.db.refresh(evaluation, ["answers"])
            missing = incomplete_items(
                template.items if template else [], evaluation.answers
            )
            if missing:
                raise EvaluationIncompleteException(missing)
        return await self._transition(
            evaluation, EvaluationStatus.draft, EvaluationStatus.saved, "save"
        )

    async def publish_evaluation(self, evaluation: Evaluation) -> Evaluation:
        """Saved to published — the only step that exposes it to the member."""
        return await self._transition(
            evaluation, EvaluationStatus.saved, EvaluationStatus.published, "publish"
        )

    async def unpublish_evaluation(self, evaluation: Evaluation) -> Evaluation:
        """Published back to saved."""
        return await self._transition(
            evaluation, EvaluationStatus.published, EvaluationStatus.saved, "unpublish"
        )

    async def revert_evaluation(self, evaluation: Evaluation) -> Evaluation:
        """Saved back to draft."""
        return await self._transition(
            evaluation, EvaluationStatus.saved, EvaluationStatus.draft, "revert"
        )

    async def transfer_evaluation(self, evaluation: Evaluation, owner: str) -> str:
        """Hand an unpublished evaluation to another coach. Returns the previous owner."""
        if evaluation.status == EvaluationStatus.published.value:
            raise InvalidStateException("Cannot transfer published evaluations")
        _ = await self.get_user_or_raise(owner)
        # Transfer is not a way around the scope rules (R37, R45).
        await self.eligibility.check(
            owner,
            evaluation.created_for,
            evaluation.event_id,
            evaluation.period_start_utc,
            evaluation.period_end_utc,
        )
        await self._refuse_twin(evaluation, owner)
        previous = evaluation.effective_owner
        evaluation.owner = owner
        evaluation.updated_at = now_utc_ms()
        await self.db.flush()
        return previous

    async def soft_delete_evaluation(self, evaluation: Evaluation) -> Evaluation:
        """Soft delete. Only a draft may be deleted (R24)."""
        if evaluation.status != EvaluationStatus.draft.value:
            raise InvalidStateException("Only draft evaluations can be deleted")
        now = now_utc_ms()
        evaluation.deleted_at = now
        evaluation.updated_at = now
        await self.db.flush()
        return evaluation

    async def restore_evaluation(self, evaluation: Evaluation) -> Evaluation:
        """Restore a soft-deleted evaluation, unless its twin is live (R7)."""
        if evaluation.deleted_at is None:
            raise NothingToRestoreException("Evaluation", evaluation.id)
        await self._refuse_twin(evaluation, evaluation.effective_owner)
        evaluation.deleted_at = None
        evaluation.updated_at = now_utc_ms()
        await self.db.flush()
        return evaluation

    async def hard_delete_evaluation(self, evaluation_id: int) -> str:
        """Hard delete a soft-deleted evaluation. Returns its member.

        A member copy it still links is soft-deleted rather than orphaned.
        """
        evaluation = await self.get_or_raise(evaluation_id, include_deleted=True)
        if evaluation.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("Evaluation", evaluation_id)
        member = evaluation.created_for
        await retire_member_copies(self.db, evaluation_id)
        await self.db.delete(evaluation)
        await self.db.flush()
        return member

    async def list_owned(
        self,
        username: str,
        *,
        deleted: bool = False,
        status: str | None = None,
        created_for: str | None = None,
        event_id: int | None = None,
        general: bool | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Evaluation], int]:
        """The caller's own evaluations, filtered; newest first (R42, R57, R58)."""
        owner = func.coalesce(Evaluation.owner, Evaluation.created_by)
        conditions = [
            owner == username,
            Evaluation.deleted_at.isnot(None)
            if deleted
            else Evaluation.deleted_at.is_(None),
        ]
        if status:
            conditions.append(Evaluation.status == status)
        if created_for:
            conditions.append(Evaluation.created_for == created_for)
        if event_id is not None:
            conditions.append(Evaluation.event_id == event_id)
        if general is True:
            conditions.append(Evaluation.event_id.is_(None))
        elif general is False:
            conditions.append(Evaluation.event_id.isnot(None))

        # The total counts the filtered set, not every evaluation in the
        # system — the archived implementation counted the lot (R60).
        total = await self.db.execute(
            select(func.count()).select_from(Evaluation).where(*conditions)
        )
        order = (
            (Evaluation.deleted_at.desc(), Evaluation.id.desc())
            if deleted
            else (Evaluation.created_at.desc(), Evaluation.id.desc())
        )
        result = await self.db.execute(
            select(Evaluation)
            .where(*conditions)
            .order_by(*order)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total.scalar_one()
