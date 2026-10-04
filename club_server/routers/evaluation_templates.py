"""Evaluation template endpoints (#302, #535).

Readable and writable by any staff member — an admin or a coach (R46); a
live template's name is unique (R49a). Item edits and the item search live
in ``evaluation_template_items.py``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_evaluations_enabled,
    require_super_admin,
)
from ..schemas.common import PaginatedResponse
from ..schemas.evaluation_template import (
    EvaluationTemplateCreate,
    EvaluationTemplateResponse,
    EvaluationTemplateUpdate,
)
from ..services.audit import AuditDetails, AuditService
from ..services.audit_actions import AuditAction
from ..services.evaluation_template import EvaluationTemplateService
from ..utils import get_client_ip
from . import evaluation_errors as err

router = APIRouter(
    prefix="/evaluations/templates",
    tags=["evaluation-templates"],
    dependencies=[Depends(require_evaluations_enabled)],
)


async def audit_template(
    db: AsyncSession,
    request: Request,
    actor: User,
    action: AuditAction,
    template_id: int,
    details: AuditDetails | None = None,
) -> None:
    """One audit row against a template."""
    await AuditService(db).log(
        actor_username=actor.username,
        action=action,
        resource_type="evaluation_template",
        resource_id=str(template_id),
        details=details,
        ip_address=get_client_ip(request),
    )


@router.get("", response_model=PaginatedResponse[EvaluationTemplateResponse])
async def list_templates(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[EvaluationTemplateResponse]:
    """List live templates."""
    return await EvaluationTemplateService(db).list_templates(
        offset=offset, limit=limit
    )


@router.get("/deleted", response_model=PaginatedResponse[EvaluationTemplateResponse])
async def list_deleted_templates(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[EvaluationTemplateResponse]:
    """List soft-deleted templates."""
    return await EvaluationTemplateService(db).list_deleted_templates(
        offset=offset, limit=limit
    )


@router.get("/by_id/{template_id}", response_model=EvaluationTemplateResponse)
async def get_template(
    template_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Read one template, its items in layout order."""
    try:
        template = await EvaluationTemplateService(db).get_or_raise(template_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return await EvaluationTemplateService(db).response(template)


@router.post(
    "", response_model=EvaluationTemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_template(
    payload: EvaluationTemplateCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Create a template whole; items arrive inline in its layout (R49)."""
    try:
        template = await EvaluationTemplateService(db).create_template(
            payload, created_by=current_user.username
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db,
        request,
        current_user,
        AuditAction.CREATE_EVALUATION_TEMPLATE,
        template.id,
        {"name": template.name},
    )
    return await EvaluationTemplateService(db).response(template)


@router.patch("/by_id/{template_id}", response_model=EvaluationTemplateResponse)
async def update_template(
    template_id: int,
    payload: EvaluationTemplateUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Rename, or re-lay out a template no evaluation uses (R12c, R27)."""
    try:
        template = await EvaluationTemplateService(db).update_template(
            template_id, name=payload.name, layout=payload.layout
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db,
        request,
        current_user,
        AuditAction.UPDATE_EVALUATION_TEMPLATE,
        template_id,
        {"name": template.name},
    )
    return await EvaluationTemplateService(db).response(template)


@router.delete("/by_id/{template_id}", response_model=EvaluationTemplateResponse)
async def soft_delete_template(
    template_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Soft delete a template, returning the updated entity."""
    try:
        template = await EvaluationTemplateService(db).soft_delete_template(template_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db, request, current_user, AuditAction.DELETE_EVALUATION_TEMPLATE, template_id
    )
    return await EvaluationTemplateService(db).response(template)


@router.post("/by_id/{template_id}/restore", response_model=EvaluationTemplateResponse)
async def restore_template(
    template_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Restore a soft-deleted template."""
    try:
        template = await EvaluationTemplateService(db).restore_template(template_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db, request, current_user, AuditAction.RESTORE_EVALUATION_TEMPLATE, template_id
    )
    return await EvaluationTemplateService(db).response(template)


@router.delete("/by_id/{template_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_template(
    template_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> None:
    """Hard delete a template. Super-admin only (R47)."""
    try:
        await EvaluationTemplateService(db).hard_delete_template(template_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db,
        request,
        current_user,
        AuditAction.HARD_DELETE_EVALUATION_TEMPLATE,
        template_id,
    )
