from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..utils import get_client_ip
from ..dependencies import (
    get_db,
    get_authenticated_user,
    get_current_active_user,
    require_admin,
)
from ..schemas.notification import (
    NotificationCreate,
    NotificationResponse,
    NotificationPrefResponse,
    NotificationPrefUpdate,
)
from ..schemas.common import PaginatedResponse, UnreadCountResponse
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.notification import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=PaginatedResponse[NotificationResponse])
async def list_notifications(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: Annotated[bool, Query(alias="unreadOnly")] = False,
):
    """List notifications for the current user."""
    notification_service = NotificationService(db)
    return await notification_service.list_notifications(
        username=current_user.username,
        offset=offset,
        limit=limit,
        unread_only=unread_only,
    )


@router.get("/pending-actions", response_model=PaginatedResponse[NotificationResponse])
async def list_pending_actions(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List the current user's actionable notifications whose linked
    pending action is still unresolved. Notifications auto-dismiss from
    this feed once the source row reaches a terminal state."""
    notification_service = NotificationService(db)
    return await notification_service.list_pending_actions(
        username=current_user.username,
        offset=offset,
        limit=limit,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> UnreadCountResponse:
    """Get count of unread notifications."""
    notification_service = NotificationService(db)
    count = await notification_service.get_unread_count(current_user.username)
    return UnreadCountResponse(count=count)


@router.post("/by_id/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Mark a notification as read."""
    notification_service = NotificationService(db)

    await notification_service.mark_read(notification_id, current_user.username)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Mark all notifications as read."""
    notification_service = NotificationService(db)
    await notification_service.mark_all_read(current_user.username)


@router.get("/preferences", response_model=NotificationPrefResponse)
async def get_preferences(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Get notification preferences for the current user."""
    notification_service = NotificationService(db)
    return await notification_service.get_preferences(current_user.username)


@router.patch("/preferences", response_model=NotificationPrefResponse)
async def update_preferences(
    request: NotificationPrefUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Update notification preferences."""
    notification_service = NotificationService(db)
    return await notification_service.update_preferences(
        username=current_user.username,
        email_enabled=request.email_enabled,
        push_enabled=request.push_enabled,
        sms_enabled=request.sms_enabled,
    )


@router.post(
    "", response_model=NotificationResponse, status_code=status.HTTP_201_CREATED
)
async def create_notification(
    request: Request,
    data: NotificationCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Create a notification for a user (admin only)."""
    notification_service = NotificationService(db)
    audit_service = AuditService(db)

    notification = await notification_service.create_notification(
        username=data.username,
        notification_type=data.type,
        channel=data.channel,
        payload=data.payload,
        pending_action_type=data.pending_action_type,
        pending_action_id=data.pending_action_id,
        pending_action_key=data.pending_action_key,
    )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.CREATE_NOTIFICATION,
        target_username=data.username,
        resource_type="notification",
        resource_id=str(notification.id),
        details={"type": data.type},
        ip_address=get_client_ip(request),
    )

    return NotificationResponse.from_model(notification)


@router.delete("/by_id/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    request: Request,
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Delete a notification (admin only)."""
    notification_service = NotificationService(db)
    audit_service = AuditService(db)

    await notification_service.delete_notification(notification_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.DELETE_NOTIFICATION,
        resource_type="notification",
        resource_id=str(notification_id),
        ip_address=get_client_ip(request),
    )
