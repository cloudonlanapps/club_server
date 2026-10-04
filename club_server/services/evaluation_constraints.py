"""What an evaluation's key and period must satisfy (#535, R4, R7).

A review looks back: its period ends at or before the server's clock when
the period is written (R4). The ordering of the bounds is the request
schema's, since it needs no clock. And among one effective owner's live
evaluations, at most one reviews a given member on a given template over a
given period, no period counting as a value (R7).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation
from ..exceptions import (
    EvaluationDuplicateException,
    EvaluationPeriodInFutureException,
)
from ..utils import now_utc_ms


def refuse_future_period(period_end_utc: int | None) -> None:
    """A period ending later than now is refused (R4)."""
    if period_end_utc is not None and period_end_utc > now_utc_ms():
        raise EvaluationPeriodInFutureException(period_end_utc)


async def refuse_duplicate(
    db: AsyncSession,
    *,
    owner: str,
    created_for: str,
    template_id: int,
    period_start_utc: int | None,
    period_end_utc: int | None,
    own_id: int | None = None,
) -> None:
    """Refuse a second live review of one key for one effective owner (R7).

    ``own_id`` is the evaluation being changed, moved or restored, which
    does not collide with itself. The event is not part of the key.
    """
    query = select(Evaluation.id).where(
        func.coalesce(Evaluation.owner, Evaluation.created_by) == owner,
        Evaluation.created_for == created_for,
        Evaluation.template_id == template_id,
        Evaluation.period_start_utc.is_not_distinct_from(period_start_utc),
        Evaluation.period_end_utc.is_not_distinct_from(period_end_utc),
        Evaluation.deleted_at.is_(None),
    )
    if own_id is not None:
        query = query.where(Evaluation.id != own_id)
    twin = await db.execute(query.limit(1))
    if twin.scalar_one_or_none() is not None:
        raise EvaluationDuplicateException()
