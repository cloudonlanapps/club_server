from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_current_active_user, get_db, require_self_or_staff
from ..exceptions import (
    AlreadyMemberException,
    AutoGroupNotJoinableException,
    JoinRequestNotPendingException,
    JoinRequestPendingException,
    NotEligibleException,
)
from ..schemas.group import GroupResponse
from ..schemas.group_join_request import JoinRequestCreate, JoinRequestResponse
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.group import GroupService
from ..utils import get_client_ip

router = APIRouter(prefix="/mygroups", tags=["My Groups"])


@router.get("/by_id/{username}", response_model=list[GroupResponse])
async def list_my_groups(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Groups the user belongs to (explicit memberships + matching auto groups)."""
    require_self_or_staff(username, current_user)
    group_service = GroupService(db)
    return await group_service.get_user_groups(username)


@router.get("/by_id/{username}/group/{group_id}", response_model=GroupResponse)
async def get_my_group(
    username: str,
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Read a single group's info if the user is a member or has a join request."""
    require_self_or_staff(username, current_user)
    group_service = GroupService(db)
    return await group_service.get_user_group(username, group_id)


@router.get("/by_id/{username}/eligible", response_model=list[GroupResponse])
async def list_my_eligible_groups(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Groups the user can request to join."""
    require_self_or_staff(username, current_user)
    group_service = GroupService(db)
    return await group_service.list_eligible_groups(username)


@router.post(
    "/by_id/{username}/join/{group_id}",
    response_model=JoinRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_join_request(
    request: Request,
    username: str,
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    body: JoinRequestCreate | None = None,
):
    """Submit a join request for the user."""
    require_self_or_staff(username, current_user)
    group_service = GroupService(db)
    audit_service = AuditService(db)

    reason = body.reason if body is not None else None

    try:
        req = await group_service.create_join_request(group_id, username, reason)
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.CREATE_GROUP_JOIN_REQUEST,
            target_username=username,
            resource_type="group_join_request",
            resource_id=str(req.id),
            details={"group_id": group_id},
            ip_address=get_client_ip(request),
        )
        return JoinRequestResponse.from_model(req)
    except AutoGroupNotJoinableException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AUTO_GROUP_NOT_JOINABLE",
                "message": (
                    f"Auto group {group_id} has no joinable list; "
                    "membership is computed from criteria"
                ),
            },
        )
    except AlreadyMemberException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_MEMBER",
                "message": f"User {username} is already a member of group {group_id}",
            },
        )
    except JoinRequestPendingException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REQUEST_PENDING",
                "message": (
                    f"User {username} already has a pending join request for "
                    f"group {group_id}"
                ),
            },
        )
    except NotEligibleException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "NOT_ELIGIBLE", "message": str(e)},
        )


@router.get(
    "/by_id/{username}/requests",
    response_model=list[JoinRequestResponse],
)
async def list_my_requests(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """List join requests submitted by the user."""
    require_self_or_staff(username, current_user)
    group_service = GroupService(db)
    requests = await group_service.list_user_requests(username)
    return [JoinRequestResponse.from_model(r) for r in requests]


@router.delete(
    "/by_id/{username}/requests/{request_id}",
    response_model=JoinRequestResponse,
)
async def cancel_my_request(
    request: Request,
    username: str,
    request_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Cancel a pending join request the user submitted."""
    require_self_or_staff(username, current_user)
    group_service = GroupService(db)
    audit_service = AuditService(db)

    try:
        req = await group_service.cancel_join_request(request_id, username)
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.CANCEL_GROUP_JOIN_REQUEST,
            target_username=username,
            resource_type="group_join_request",
            resource_id=str(request_id),
            details={"group_id": req.group_id},
            ip_address=get_client_ip(request),
        )
        return JoinRequestResponse.from_model(req)
    except JoinRequestNotPendingException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "JOIN_REQUEST_NOT_PENDING", "message": str(e)},
        )
