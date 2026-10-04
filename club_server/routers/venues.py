from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..utils import get_client_ip
from ..dependencies import (
    get_current_active_user,
    get_db,
    require_admin,
    require_super_admin,
)
from ..schemas.common import PaginatedResponse
from ..schemas.venue import (
    VenueCreate,
    VenueResponse,
    VenueUpdate,
)
from ..exceptions import (
    DefaultVenueExistsException,
    VenueHasEventsException,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.venue import VenueService

router = APIRouter(prefix="/venues", tags=["Venues"])


@router.get("", response_model=PaginatedResponse[VenueResponse])
async def list_venues(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List live venues for any logged-in user (venue R9; the website uses /public)."""
    venue_service = VenueService(db)
    return await venue_service.get_venues(offset=offset, limit=limit)


@router.get("/deleted", response_model=PaginatedResponse[VenueResponse])
async def list_deleted_venues(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List soft-deleted venues (admin only)."""
    venue_service = VenueService(db)
    return await venue_service.list_deleted_venues(offset=offset, limit=limit)


@router.get("/by_id/{venue_id}", response_model=VenueResponse)
async def get_venue(
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get venue by ID for any logged-in user (venue R8; the website uses /public)."""
    venue_service = VenueService(db)
    venue = await venue_service.get_venue(venue_id)
    return VenueResponse.from_model(venue)


@router.post("", response_model=VenueResponse, status_code=status.HTTP_201_CREATED)
async def create_venue(
    request: Request,
    data: VenueCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Create a new venue (admin only)."""
    venue_service = VenueService(db)
    audit_service = AuditService(db)

    try:
        venue = await venue_service.create_venue(
            name=data.name,
            is_default=data.is_default,
            address=data.address,
            description=data.description,
            map_uri=data.map_uri,
            is_featured=data.is_featured,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.CREATE_VENUE,
            resource_type="venue",
            resource_id=str(venue.id),
            details={"name": data.name},
            ip_address=get_client_ip(request),
        )

        return VenueResponse.from_model(venue)
    except DefaultVenueExistsException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DEFAULT_VENUE_EXISTS",
                "message": "A default venue already exists",
            },
        )


@router.patch("/by_id/{venue_id}", response_model=VenueResponse)
async def update_venue(
    request: Request,
    venue_id: int,
    data: VenueUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Update a venue (admin only)."""
    venue_service = VenueService(db)
    audit_service = AuditService(db)

    fields_set = {
        schema_field
        for schema_field in ("address", "description", "map_uri")
        if schema_field in data.model_fields_set
    }

    venue, changes = await venue_service.update_venue(
        venue_id,
        name=data.name,
        is_default=data.is_default,
        address=data.address,
        description=data.description,
        map_uri=data.map_uri,
        is_featured=data.is_featured,
        fields_set=fields_set,
    )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.UPDATE_VENUE,
        resource_type="venue",
        resource_id=str(venue.id),
        details=changes.to_audit_dict(),
        ip_address=get_client_ip(request),
    )

    return VenueResponse.from_model(venue)


@router.post("/by_id/{venue_id}/restore", response_model=VenueResponse)
async def restore_venue(
    request: Request,
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Restore a soft-deleted venue (admin only)."""
    venue_service = VenueService(db)
    audit_service = AuditService(db)

    venue = await venue_service.restore_venue(venue_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.RESTORE_VENUE,
        resource_type="venue",
        resource_id=str(venue.id),
        ip_address=get_client_ip(request),
    )

    return VenueResponse.from_model(venue)


@router.delete("/by_id/{venue_id}", response_model=VenueResponse)
async def delete_venue(
    request: Request,
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Soft delete a venue (admin only)."""
    venue_service = VenueService(db)
    audit_service = AuditService(db)

    try:
        venue = await venue_service.soft_delete_venue(venue_id)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.SOFT_DELETE_VENUE,
            resource_type="venue",
            resource_id=str(venue_id),
            ip_address=get_client_ip(request),
        )

        return VenueResponse.from_model(venue)
    except VenueHasEventsException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VENUE_HAS_EVENTS",
                "message": f"Cannot delete venue: {e.event_count} events reference this venue",
            },
        )


@router.delete("/by_id/{venue_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_venue(
    request: Request,
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """
    Wipeout a venue (hard delete). SuperAdmin only.

    Permanently removes venue from database.
    Blocked if ANY events reference this venue (past or future).
    """
    venue_service = VenueService(db)
    audit_service = AuditService(db)

    try:
        deletion_info = await venue_service.hard_delete_venue(venue_id)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.WIPEOUT_VENUE,
            resource_type="venue",
            resource_id=str(venue_id),
            details=deletion_info.model_dump(),
            ip_address=get_client_ip(request),
        )
    except VenueHasEventsException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VENUE_HAS_EVENTS",
                "message": f"Cannot delete venue: {e.event_count} events reference this venue. Delete events first.",
            },
        )
