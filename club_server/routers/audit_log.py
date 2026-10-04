from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_current_active_user, get_db, is_admin_or_coach
from ..schemas.audit_log import AuditLogResponse
from ..services.audit_log_query import AuditLogQueryService

router = APIRouter(prefix="/audit_log", tags=["AuditLog"])


@router.get("", response_model=AuditLogResponse)
async def list_audit_log(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    offset: int = Query(0, ge=0),
    limit: int = Query(50),
    actor: str | None = Query(None),
    action: str | None = Query(None),
    from_ts: int | None = Query(None),
    to_ts: int | None = Query(None),
    username: str | None = Query(
        None, description="Scope to a user as either actor or target."
    ),
    resource_type: str | None = Query(
        None, description="Scope to an entity type (with resource_id)."
    ),
    resource_id: str | None = Query(
        None, description="Scope to an entity id (with resource_type)."
    ),
    verbose: int = Query(
        1,
        ge=1,
        le=3,
        description=(
            "Breadth of an entity scope: 1 = the entity's own rows, "
            "2 = also its media-link rows, 3 = also its occurrence rows "
            "(events only). Ignored for the global feed and username scope."
        ),
    ),
) -> AuditLogResponse:
    """Return paginated audit log with foreign-key ids resolved.

    Two access modes share this endpoint:

    - **Global feed** (no scope params): super-admin only — the full,
      unfiltered history.
    - **Entity-scoped** (``username`` or ``resource_type``+``resource_id``):
      any admin or coach, regardless of ownership, for transparency.

    ``resource_type`` and ``resource_id`` must be supplied together.
    """
    scoped = (
        username is not None or resource_type is not None or resource_id is not None
    )
    if scoped:
        if not is_admin_or_coach(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INSUFFICIENT_PERMISSION",
                    "message": "Requires admin or coach to view scoped audit history",
                },
            )
    elif not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INSUFFICIENT_PERMISSION",
                "message": "Requires super admin privileges for the global audit feed",
            },
        )

    if (resource_type is None) != (resource_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_RESOURCE_SCOPE",
                "message": "resource_type and resource_id must be provided together",
            },
        )

    service = AuditLogQueryService(db)
    return await service.query(
        offset=offset,
        limit=limit,
        actor=actor,
        action=action,
        from_ts=from_ts,
        to_ts=to_ts,
        username=username,
        resource_type=resource_type,
        resource_id=resource_id,
        verbose=verbose,
    )
