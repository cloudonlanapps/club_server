"""Event Marketing module: staff endpoints (#410, marketing R5–R9)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_event_marketing_enabled,
    require_organizer_or_admin,
)
from ..schemas.event_marketing_extended import (
    EventMarketingResponse,
    EventMarketingWrite,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.event import EventService
from ..services.event_marketing import EventMarketingService
from ..utils import get_client_ip

router = APIRouter(
    prefix="/events/by_id/{event_id}/marketing",
    tags=["Event Marketing"],
    dependencies=[Depends(require_event_marketing_enabled)],
)


@router.get("", response_model=EventMarketingResponse)
async def get_event_marketing(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EventMarketingResponse:
    """Read the extended block (admin, coach); 404 when the event has none."""
    await EventService(db).get_event(event_id)
    return await EventMarketingService(db).get(event_id)


@router.put("", response_model=EventMarketingResponse)
async def replace_event_marketing(
    event_id: int,
    data: EventMarketingWrite,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EventMarketingResponse:
    """Replace the whole block (admin or organizer); 404 for a deleted event."""
    event = await EventService(db).get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    response = await EventMarketingService(db).replace(event_id, data)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.UPDATE_EVENT_MARKETING,
        resource_type="event",
        resource_id=str(event_id),
        details={"fields": sorted(data.model_dump(exclude_none=True))},
        ip_address=get_client_ip(request),
    )
    return response


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event_marketing(
    event_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> None:
    """Remove the block (admin or organizer); 404 for a deleted event."""
    event = await EventService(db).get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    await EventMarketingService(db).delete(event_id)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.DELETE_EVENT_MARKETING,
        resource_type="event",
        resource_id=str(event_id),
        ip_address=get_client_ip(request),
    )
