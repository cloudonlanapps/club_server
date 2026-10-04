"""Admin inbox for inquiries (#407, public R28)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_admin
from ..schemas.common import PaginatedResponse
from ..schemas.inquiry import InquiryHandledUpdate, InquiryResponse
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.inquiry import InquiryService
from ..utils import get_client_ip

router = APIRouter(prefix="/admin/inquiries", tags=["Admin"])


@router.get("", response_model=PaginatedResponse[InquiryResponse])
async def list_inquiries(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    kind: Annotated[Literal["contact", "interest"] | None, Query()] = None,
    handled: Annotated[bool | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[InquiryResponse]:
    """Newest first; filter by kind and handled state."""
    return await InquiryService(db).list_inquiries(
        kind=kind, handled=handled, offset=offset, limit=limit
    )


@router.patch("/{inquiry_id}", response_model=InquiryResponse)
async def set_inquiry_handled(
    inquiry_id: int,
    data: InquiryHandledUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> InquiryResponse:
    """Mark handled (by the caller, now) or reopen."""
    response = await InquiryService(db).set_handled(
        inquiry_id, data.handled, current_user.username
    )
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.HANDLE_INQUIRY,
        resource_type="inquiry",
        resource_id=str(inquiry_id),
        details={"handled": data.handled},
        ip_address=get_client_ip(request),
    )
    return response


@router.delete("/{inquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inquiry(
    inquiry_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> None:
    """Hard delete: the row is PII."""
    await InquiryService(db).delete(inquiry_id)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.DELETE_INQUIRY,
        resource_type="inquiry",
        resource_id=str(inquiry_id),
        ip_address=get_client_ip(request),
    )
