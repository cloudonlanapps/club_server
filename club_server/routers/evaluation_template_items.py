"""Template items, edited one at a time, and the cross-template item search (#535).

Writes are any staff member's (R46), refused while an evaluation uses the
template (R27). The search is a staff read (R46a).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation_template_item import EvaluationItemType
from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_evaluations_enabled,
)
from ..schemas.common import PaginatedResponse
from ..schemas.evaluation_item import EvaluationTemplateItemSchema
from ..schemas.evaluation_template import (
    EvaluationTemplateItemAdd,
    EvaluationTemplateItemHit,
    EvaluationTemplateResponse,
)
from ..services.audit_actions import AuditAction
from ..services.evaluation_template import EvaluationTemplateService
from ..services.evaluation_template_items import EvaluationTemplateItemService
from . import evaluation_errors as err
from .evaluation_templates import audit_template

router = APIRouter(
    prefix="/evaluations/templates",
    tags=["evaluation-templates"],
    dependencies=[Depends(require_evaluations_enabled)],
)


@router.get("/items", response_model=PaginatedResponse[EvaluationTemplateItemHit])
async def search_items(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
    search: str | None = None,
    item_type: Annotated[EvaluationItemType | None, Query(alias="type")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[EvaluationTemplateItemHit]:
    """Items of every live template, to copy an existing question (R46a)."""
    return await EvaluationTemplateItemService(db).search_items(
        search, item_type.value if item_type else None, offset, limit
    )


@router.post(
    "/by_id/{template_id}/items",
    response_model=EvaluationTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_item(
    template_id: int,
    payload: EvaluationTemplateItemAdd,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Add one item — new, or a copy naming its origin (R12b, R26a)."""
    try:
        template, item_id = await EvaluationTemplateItemService(db).add_item(
            template_id, payload.item, payload.section
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db,
        request,
        current_user,
        AuditAction.UPDATE_EVALUATION_TEMPLATE,
        template_id,
        {"addedItemId": item_id},
    )
    return await EvaluationTemplateService(db).response(template)


@router.put(
    "/by_id/{template_id}/items/{item_id}", response_model=EvaluationTemplateResponse
)
async def replace_item(
    template_id: int,
    item_id: int,
    payload: EvaluationTemplateItemSchema,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Replace one item in place; its id, type and origin stay (R26a)."""
    try:
        template = await EvaluationTemplateItemService(db).replace_item(
            template_id, item_id, payload
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db,
        request,
        current_user,
        AuditAction.UPDATE_EVALUATION_TEMPLATE,
        template_id,
        {"replacedItemId": item_id},
    )
    return await EvaluationTemplateService(db).response(template)


@router.delete(
    "/by_id/{template_id}/items/{item_id}", response_model=EvaluationTemplateResponse
)
async def remove_item(
    template_id: int,
    item_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationTemplateResponse:
    """Remove one item and its place in the layout (R26a)."""
    try:
        template = await EvaluationTemplateItemService(db).remove_item(
            template_id, item_id
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    await audit_template(
        db,
        request,
        current_user,
        AuditAction.UPDATE_EVALUATION_TEMPLATE,
        template_id,
        {"removedItemId": item_id},
    )
    return await EvaluationTemplateService(db).response(template)
