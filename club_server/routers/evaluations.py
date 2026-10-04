"""Evaluation endpoints — create, read, update, delete (#302, #535).

Only coaches write evaluations (R34, R45). On the staff surface an
evaluation exists only for its effective owner (R38a, R42); answers live in
``evaluation_answers.py`` and the lifecycle in ``evaluation_lifecycle.py``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation, EvaluationStatus
from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_coach,
    require_evaluations_enabled,
    require_super_admin,
)
from ..schemas.common import PaginatedResponse
from ..schemas.evaluation import (
    EvaluationCreate,
    EvaluationStaffView,
    EvaluationUpdate,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.evaluation import EvaluationService
from ..services.evaluation_pdf import member_copy_pdf
from ..services.evaluation_views import member_view, staff_view
from ..utils import get_client_ip
from . import evaluation_errors as err

PDF_MEDIA_TYPE = "application/pdf"

router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(require_evaluations_enabled)],
)


async def load_owned(
    service: EvaluationService,
    evaluation_id: int,
    current_user: User,
    include_deleted: bool = False,
) -> Evaluation:
    """The caller's own evaluation; for anyone else it does not exist (R35, R38a)."""
    try:
        return await service.get_owned_or_raise(
            evaluation_id, current_user.username, include_deleted
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)


async def _page(
    db: AsyncSession, evaluations: list[Evaluation], total: int, offset: int, limit: int
) -> PaginatedResponse[EvaluationStaffView]:
    return PaginatedResponse(
        items=[await staff_view(db, e) for e in evaluations],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("", response_model=PaginatedResponse[EvaluationStaffView])
async def list_evaluations(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[EvaluationStatus | None, Query(alias="status")] = None,
    created_for: Annotated[str | None, Query(alias="createdFor")] = None,
    event_id: Annotated[int | None, Query(alias="eventId")] = None,
    general: bool | None = None,
) -> PaginatedResponse[EvaluationStaffView]:
    """The caller's own evaluations; an admin owns none (R42, R57, R58)."""
    evaluations, total = await EvaluationService(db).list_owned(
        current_user.username,
        status=status_filter.value if status_filter else None,
        created_for=created_for,
        event_id=event_id,
        general=general,
        offset=offset,
        limit=limit,
    )
    return await _page(db, evaluations, total, offset, limit)


@router.get("/deleted", response_model=PaginatedResponse[EvaluationStaffView])
async def list_deleted_evaluations(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[EvaluationStaffView]:
    """The caller's own soft-deleted evaluations (R25)."""
    evaluations, total = await EvaluationService(db).list_owned(
        current_user.username, deleted=True, offset=offset, limit=limit
    )
    return await _page(db, evaluations, total, offset, limit)


@router.get("/by_id/{evaluation_id}", response_model=EvaluationStaffView)
async def get_evaluation(
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Read one evaluation whole, private items included (R41)."""
    evaluation = await load_owned(EvaluationService(db), evaluation_id, current_user)
    return await staff_view(db, evaluation)


@router.get(
    "/by_id/{evaluation_id}/pdf",
    response_class=Response,
    responses={200: {"content": {PDF_MEDIA_TYPE: {}}}},
)
async def preview_member_copy(
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> Response:
    """Preview the member copy at any status; generated, never stored (R63a)."""
    evaluation = await load_owned(EvaluationService(db), evaluation_id, current_user)
    content = await member_copy_pdf(db, await member_view(db, evaluation))
    return Response(
        content=content,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'inline; filename="evaluation-{evaluation_id}.pdf"'
        },
    )


@router.post(
    "", response_model=EvaluationStaffView, status_code=status.HTTP_201_CREATED
)
async def create_evaluation(
    payload: EvaluationCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_coach())],
) -> EvaluationStaffView:
    """Create a draft, created by and owned by the calling coach (R34, R50).

    Not audited: an evaluation enters the trail when it is first saved (R52).
    """
    try:
        evaluation = await EvaluationService(db).create_evaluation(
            payload, current_user.username
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return await staff_view(db, evaluation)


@router.patch("/by_id/{evaluation_id}", response_model=EvaluationStaffView)
async def update_evaluation(
    evaluation_id: int,
    payload: EvaluationUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Change a draft's event or period; the member is fixed (R21, R23)."""
    service = EvaluationService(db)
    evaluation = await load_owned(service, evaluation_id, current_user)
    if payload.model_fields_set:
        try:
            evaluation = await service.update_draft(evaluation, payload)
        except err.DOMAIN_ERRORS as exc:
            raise err.to_http(exc)
    return await staff_view(db, evaluation)


@router.delete("/by_id/{evaluation_id}", response_model=EvaluationStaffView)
async def soft_delete_evaluation(
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Soft delete a draft, returning the updated entity (R24, R25)."""
    service = EvaluationService(db)
    evaluation = await load_owned(service, evaluation_id, current_user)
    try:
        evaluation = await service.soft_delete_evaluation(evaluation)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return await staff_view(db, evaluation)


@router.post("/by_id/{evaluation_id}/restore", response_model=EvaluationStaffView)
async def restore_evaluation(
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Restore the caller's soft-deleted evaluation, unless its twin is live (R7)."""
    service = EvaluationService(db)
    evaluation = await load_owned(
        service, evaluation_id, current_user, include_deleted=True
    )
    try:
        evaluation = await service.restore_evaluation(evaluation)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return await staff_view(db, evaluation)


@router.delete("/by_id/{evaluation_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_evaluation(
    evaluation_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> None:
    """Hard delete an evaluation. Super-admin only (R47), and logged (R52)."""
    try:
        member = await EvaluationService(db).hard_delete_evaluation(evaluation_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)

    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.HARD_DELETE_EVALUATION,
        target_username=member,
        resource_type="evaluation",
        resource_id=str(evaluation_id),
        ip_address=get_client_ip(request),
    )
