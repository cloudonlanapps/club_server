"""Evaluation lifecycle endpoints (#302, #535).

The four permitted transitions plus transfer — the moves the audit trail
records (R52). Publication and withdrawal notify the member; transfer
notifies the receiving coach (R53, R54).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation
from ..db.models.user import User
from ..dependencies import (
    get_db,
    is_admin,
    require_admin_or_coach,
    require_evaluations_enabled,
)
from ..schemas.evaluation import EvaluationStaffView, EvaluationTransferRequest
from ..services.audit import AuditDetails, AuditService
from ..services.audit_actions import AuditAction
from ..services.evaluation import EvaluationService
from ..services.evaluation_member_copy import store_member_copy
from ..services.evaluation_views import staff_view
from ..services.notification import NotificationEvent, NotificationService
from ..utils import get_client_ip
from . import evaluation_errors as err
from .evaluations import load_owned

router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(require_evaluations_enabled)],
)


async def _audit(
    db: AsyncSession,
    request: Request,
    actor: User,
    action: AuditAction,
    evaluation: Evaluation,
    details: AuditDetails | None = None,
) -> None:
    await AuditService(db).log(
        actor_username=actor.username,
        action=action,
        target_username=evaluation.created_for,
        resource_type="evaluation",
        resource_id=str(evaluation.id),
        details=details,
        ip_address=get_client_ip(request),
    )


async def _move(
    db: AsyncSession,
    request: Request,
    current_user: User,
    evaluation_id: int,
    step: Callable[[EvaluationService, Evaluation], Awaitable[Evaluation]],
    action: AuditAction,
) -> Evaluation:
    """Load the caller's evaluation, move it, and audit the move."""
    service = EvaluationService(db)
    evaluation = await load_owned(service, evaluation_id, current_user)
    try:
        evaluation = await step(service, evaluation)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await _audit(db, request, current_user, action, evaluation)
    return evaluation


@router.post("/by_id/{evaluation_id}/save", response_model=EvaluationStaffView)
async def save_evaluation(
    evaluation_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Draft to saved, once complete (R15)."""
    evaluation = await _move(
        db,
        request,
        current_user,
        evaluation_id,
        lambda s, e: s.save_evaluation(e),
        AuditAction.SAVE_EVALUATION,
    )
    return await staff_view(db, evaluation)


@router.post("/by_id/{evaluation_id}/publish", response_model=EvaluationStaffView)
async def publish_evaluation(
    evaluation_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Saved to published — the only step that exposes it to the member (R16).

    Stores the member copy, replacing any earlier one (R63, R63b).
    """
    evaluation = await _move(
        db,
        request,
        current_user,
        evaluation_id,
        lambda s, e: s.publish_evaluation(e),
        AuditAction.PUBLISH_EVALUATION,
    )
    _ = await store_member_copy(db, evaluation)
    _ = await NotificationService(db).notify_for_event(
        NotificationEvent(
            type="evaluation.published",
            recipients=[evaluation.created_for],
            data={
                "evaluationId": evaluation.id,
                "owner": evaluation.effective_owner,
                "eventId": evaluation.event_id,
            },
        )
    )
    return await staff_view(db, evaluation)


@router.post("/by_id/{evaluation_id}/unpublish", response_model=EvaluationStaffView)
async def unpublish_evaluation(
    evaluation_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Published back to saved. The member is told, rather than left guessing (R54)."""
    evaluation = await _move(
        db,
        request,
        current_user,
        evaluation_id,
        lambda s, e: s.unpublish_evaluation(e),
        AuditAction.UNPUBLISH_EVALUATION,
    )
    _ = await NotificationService(db).notify_for_event(
        NotificationEvent(
            type="evaluation.withdrawn",
            recipients=[evaluation.created_for],
            data={"evaluationId": evaluation.id},
        )
    )
    return await staff_view(db, evaluation)


@router.post("/by_id/{evaluation_id}/revert", response_model=EvaluationStaffView)
async def revert_evaluation(
    evaluation_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Saved back to draft, the way to edit it again (R18, R22)."""
    evaluation = await _move(
        db,
        request,
        current_user,
        evaluation_id,
        lambda s, e: s.revert_evaluation(e),
        AuditAction.REVERT_EVALUATION,
    )
    return await staff_view(db, evaluation)


@router.post("/by_id/{evaluation_id}/transfer", status_code=status.HTTP_204_NO_CONTENT)
async def transfer_evaluation(
    evaluation_id: int,
    payload: EvaluationTransferRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> None:
    """Hand an unpublished evaluation to another coach (R36).

    The effective owner may, or an admin naming it by id. Nothing of the
    evaluation is returned: after a transfer only its new owner sees it.
    """
    service = EvaluationService(db)
    try:
        if is_admin(current_user):
            evaluation = await service.get_or_raise(evaluation_id)
        else:
            evaluation = await service.get_owned_or_raise(
                evaluation_id, current_user.username
            )
        previous = await service.transfer_evaluation(evaluation, payload.owner)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)

    _ = await NotificationService(db).notify_for_event(
        NotificationEvent(
            type="evaluation.transferred",
            recipients=[payload.owner],
            data={
                "evaluationId": evaluation.id,
                "createdFor": evaluation.created_for,
                "fromOwner": previous,
            },
        )
    )
    await _audit(
        db,
        request,
        current_user,
        AuditAction.TRANSFER_EVALUATION,
        evaluation,
        {"fromOwner": previous, "toOwner": payload.owner},
    )
