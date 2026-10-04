"""Admin curation of the public staff page (#332, public R4–R5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_admin
from ..schemas.staff_listing import StaffListingRow, StaffListingUpdate
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.staff_listing import StaffListingService
from ..utils import get_client_ip

router = APIRouter(prefix="/admin/staff-listing", tags=["Admin"])


@router.get("", response_model=list[StaffListingRow])
async def list_staff_listing(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> list[StaffListingRow]:
    """Every curated coach with position, guest and hidden marks."""
    return await StaffListingService(db).list_rows()


@router.put("/{username}", response_model=StaffListingRow)
async def upsert_staff_listing(
    username: str,
    data: StaffListingUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> StaffListingRow:
    """Curate one coach. Not a profile edit: no user notice, its own audit row."""
    row = await StaffListingService(db).upsert(
        username,
        position=data.position,
        is_guest=data.is_guest,
        is_hidden=data.is_hidden,
        fields_set=data.model_fields_set,
    )
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.UPDATE_STAFF_LISTING,
        target_username=username,
        details=data.model_dump(exclude_unset=True),
        ip_address=get_client_ip(request),
    )
    return row


@router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_listing(
    username: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> None:
    """Uncurate one coach; they stay public if they consented."""
    await StaffListingService(db).delete(username)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.DELETE_STAFF_LISTING,
        target_username=username,
        ip_address=get_client_ip(request),
    )
