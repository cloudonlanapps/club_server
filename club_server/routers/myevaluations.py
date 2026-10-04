"""A member's own published evaluations (#302, #535).

A separate surface from the staff one, serving a different projection, so
private items have no route to the member at all (R39). The member reads
it, and so may any coach; an admin may not (R42).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation, EvaluationStatus
from ..db.models.user import User
from ..dependencies import (
    get_current_active_user,
    get_db,
    require_evaluations_enabled,
    require_self_or_coach,
)
from ..schemas.common import PaginatedResponse
from ..schemas.evaluation import EvaluationMemberView
from ..services.evaluation_media import list_member_visible_media
from ..services.evaluation_views import member_view
from . import evaluation_errors as err

router = APIRouter(
    prefix="/myevaluations",
    tags=["myevaluations"],
    dependencies=[Depends(require_evaluations_enabled)],
)


def _published_of(username: str) -> list[ColumnElement[bool]]:
    """The conditions for a member's live, published evaluations."""
    return [
        Evaluation.created_for == username,
        Evaluation.status == EvaluationStatus.published.value,
        Evaluation.deleted_at.is_(None),
    ]


async def _published_or_404(
    db: AsyncSession, username: str, evaluation_id: int
) -> Evaluation:
    """One published evaluation about this member.

    An unpublished one answers 404 rather than 403, so a member cannot
    detect that a coach is drafting something about them (R38).
    """
    result = await db.execute(
        select(Evaluation).where(
            Evaluation.id == evaluation_id, *_published_of(username)
        )
    )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise err.evaluation_not_found(evaluation_id)
    return evaluation


@router.get("/by_id/{username}", response_model=PaginatedResponse[EvaluationMemberView])
async def list_my_evaluations(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[EvaluationMemberView]:
    """A member's published evaluations, most recently published first (R59)."""
    require_self_or_coach(username, current_user)
    conditions = _published_of(username)
    total_result = await db.execute(
        select(func.count()).select_from(Evaluation).where(*conditions)
    )
    result = await db.execute(
        select(Evaluation)
        .where(*conditions)
        .order_by(Evaluation.published_at.desc(), Evaluation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return PaginatedResponse(
        items=[await member_view(db, e) for e in result.scalars().all()],
        total=total_result.scalar_one(),
        offset=offset,
        limit=limit,
    )


@router.get("/by_id/{username}/{evaluation_id}", response_model=EvaluationMemberView)
async def get_my_evaluation(
    username: str,
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> EvaluationMemberView:
    """Read one published evaluation about this member, in the member projection."""
    require_self_or_coach(username, current_user)
    evaluation = await _published_or_404(db, username, evaluation_id)
    return await member_view(db, evaluation)


@router.get("/by_id/{username}/{evaluation_id}/media")
async def get_my_evaluation_media(
    username: str,
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Evidence on its public questions, and the member copy (R56a, R56b, R63)."""
    require_self_or_coach(username, current_user)
    evaluation = await _published_or_404(db, username, evaluation_id)
    return await list_member_visible_media(db, evaluation, current_user)
