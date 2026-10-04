"""Admin-only broadcasts surface (#51).

Broadcasts fan out into per-recipient notification rows; recipients see
them in their normal `/v1/notifications` feed. These endpoints are
admin-authoring/reporting only.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..utils import get_client_ip
from ..dependencies import get_db, require_admin
from ..exceptions import InvalidAudienceSelectorException
from ..schemas.broadcast import (
    BroadcastCreate,
    BroadcastDetail,
    BroadcastRecipient,
    BroadcastResponse,
)
from ..schemas.common import PaginatedResponse
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.broadcast import BroadcastService

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])


def _not_found(broadcast_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "BROADCAST_NOT_FOUND",
            "message": f"Broadcast {broadcast_id} not found",
        },
    )


def _invalid_audience(exc: InvalidAudienceSelectorException) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "INVALID_AUDIENCE_SELECTOR", "message": str(exc)},
    )


@router.post("", response_model=BroadcastResponse, status_code=status.HTTP_201_CREATED)
async def create_broadcast(
    request: Request,
    data: BroadcastCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Create a broadcast and fan it out synchronously."""
    broadcast_service = BroadcastService(db)
    audit_service = AuditService(db)

    try:
        (
            broadcast,
            recipient_count,
            email_summary,
        ) = await broadcast_service.create_broadcast(
            sender_username=current_user.username,
            audience_selector=data.audience_selector,
            payload=data.payload,
            expires_at=data.expires_at_utc,
            email=data.email,
            email_subject=data.email_subject,
            email_body=data.email_body,
        )
    except InvalidAudienceSelectorException as e:
        raise _invalid_audience(e)

    details: dict[str, object] = {
        "audience": data.audience_selector,
        "recipientCount": recipient_count,
    }
    if email_summary.requested:
        details["email"] = {
            "requested": True,
            "sent": email_summary.sent,
            "skippedNoEmail": email_summary.skipped_no_email,
            "failed": email_summary.failed,
        }
    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.CREATE_BROADCAST,
        resource_type="broadcast",
        resource_id=str(broadcast.id),
        details=details,
        ip_address=get_client_ip(request),
    )

    return BroadcastResponse.from_model(broadcast, recipient_count)


@router.get("", response_model=PaginatedResponse[BroadcastResponse])
async def list_broadcasts(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List broadcasts newest-first with their resolved recipient counts."""
    _ = current_user
    broadcast_service = BroadcastService(db)
    rows = await broadcast_service.list_broadcasts(offset=offset, limit=limit)
    total = await broadcast_service.count_broadcasts()
    return PaginatedResponse(
        items=[BroadcastResponse.from_model(b, c) for (b, c) in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/by_id/{broadcast_id}", response_model=BroadcastDetail)
async def get_broadcast(
    broadcast_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    _ = current_user
    broadcast_service = BroadcastService(db)
    broadcast, read_count, unread_count = await broadcast_service.get_broadcast_detail(
        broadcast_id
    )

    return BroadcastDetail(
        id=broadcast.id,
        sender_username=broadcast.sender_username,
        audience_selector=broadcast.audience_selector,
        payload=broadcast.payload,
        sent_at_utc=broadcast.sent_at,
        expires_at_utc=broadcast.expires_at,
        status=broadcast.status,
        recipient_count=read_count + unread_count,
        read_count=read_count,
        unread_count=unread_count,
    )


@router.get(
    "/by_id/{broadcast_id}/recipients",
    response_model=PaginatedResponse[BroadcastRecipient],
)
async def list_recipients(
    broadcast_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    status_filter: Annotated[
        Literal["read", "unread"] | None, Query(alias="status")
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    _ = current_user
    broadcast_service = BroadcastService(db)
    try:
        rows, total = await broadcast_service.list_recipients(
            broadcast_id, status_filter, offset, limit
        )
    except InvalidAudienceSelectorException as e:
        raise _invalid_audience(e)

    return PaginatedResponse(
        items=[
            BroadcastRecipient(username=u, is_read=r, created_at_utc=ts)
            for (u, r, ts) in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.delete("/by_id/{broadcast_id}", response_model=BroadcastResponse)
async def revoke_broadcast(
    request: Request,
    broadcast_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Revoke a broadcast: deletes the fan-out notifications, keeps the broadcast row for audit."""
    broadcast_service = BroadcastService(db)
    audit_service = AuditService(db)

    broadcast = await broadcast_service.revoke_broadcast(broadcast_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.REVOKE_BROADCAST,
        resource_type="broadcast",
        resource_id=str(broadcast.id),
        ip_address=get_client_ip(request),
    )

    return BroadcastResponse.from_model(broadcast, recipient_count=0)
