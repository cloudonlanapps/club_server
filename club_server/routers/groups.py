from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin,
    require_admin_or_coach,
    require_super_admin,
)
from ..schemas.common import PaginatedResponse
from ..schemas.group import (
    age_band_fields,
    BulkMembersAdd,
    BulkMembersResult,
    EligibleUserInfo,
    GroupCreate,
    GroupDetailResponse,
    GroupMemberInfo,
    GroupResponse,
    GroupUpdate,
)
from ..schemas.group_join_request import (
    JoinRequestRejectBody,
    JoinRequestResponse,
)
from ..exceptions import (
    AlreadyMemberException,
    AutoGroupModificationException,
    AutoGroupNotJoinableException,
    InvalidStateException,
    JoinRequestNotPendingException,
    MembersExistException,
    MembersIneligibleException,
    NotEligibleException,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.group import GroupService, is_member_eligible
from ..utils import get_client_ip

router = APIRouter(prefix="/groups", tags=["Groups"])


def _group_not_found(group_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "GROUP_NOT_FOUND", "message": f"Group {group_id} not found"},
    )


def _auto_not_joinable(group_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "AUTO_GROUP_NOT_JOINABLE",
            "message": (
                f"Auto group {group_id} has no joinable list; "
                "membership is computed from criteria"
            ),
        },
    )


@router.get("", response_model=PaginatedResponse[GroupResponse])
async def list_groups(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List all groups (admin/coach only)."""
    group_service = GroupService(db)
    return await group_service.get_groups(offset=offset, limit=limit)


@router.get("/deleted", response_model=PaginatedResponse[GroupResponse])
async def list_deleted_groups(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List soft-deleted groups (admin only)."""
    group_service = GroupService(db)
    return await group_service.list_deleted_groups(offset=offset, limit=limit)


@router.get("/by_id/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Get group details with members (admin/coach only)."""
    group_service = GroupService(db)
    group = await group_service.get_group(group_id)

    if group.kind == "auto":
        member_objects = await group_service.get_auto_group_members(group)
    else:
        member_objects = list(group.members)

    members = [
        GroupMemberInfo(
            membername=m.membername,
            first_name=m.user.first_name if m.user else None,
            last_name=m.user.last_name if m.user else None,
            nickname=m.user.nickname if m.user else None,
            eligible=is_member_eligible(group, m.user),
        )
        for m in member_objects
    ]

    return GroupDetailResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        kind=group.kind,
        **age_band_fields(group),
        gender=group.gender,
        members=members,
        ineligible_member_count=await group_service.ineligible_member_count(group),
        created_at_utc=group.created_at,
        deleted_at_utc=group.deleted_at,
    )


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    request: Request,
    data: GroupCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Create a new group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    try:
        group = await group_service.create_group(
            name=data.name,
            description=data.description,
            min_age=data.min_age,
            max_age=data.max_age,
            strict_age=data.strict_age,
            gender=data.gender,
            semi_auto=data.semi_auto,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.CREATE_GROUP,
            resource_type="group",
            resource_id=str(group.id),
            details={"name": data.name, "kind": group.kind},
            ip_address=get_client_ip(request),
        )

        return await group_service.to_response(group, 0)
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


@router.patch("/by_id/{group_id}", response_model=GroupResponse)
async def update_group(
    request: Request,
    group_id: int,
    data: GroupUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Update a group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    fields_set: set[str] = set()
    for schema_field in (
        "description",
        "min_age",
        "max_age",
        "gender",
        "semi_auto",
    ):
        if schema_field in data.model_fields_set:
            fields_set.add(schema_field)

    try:
        group, changes = await group_service.update_group(
            group_id,
            name=data.name,
            description=data.description,
            min_age=data.min_age,
            max_age=data.max_age,
            strict_age=data.strict_age,
            gender=data.gender,
            semi_auto=data.semi_auto,
            fields_set=fields_set,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.UPDATE_GROUP,
            resource_type="group",
            resource_id=str(group.id),
            details=changes.to_audit_dict(),
            ip_address=get_client_ip(request),
        )

        member_count = len(group.members) if group.kind != "auto" else 0
        return await group_service.to_response(group, member_count)
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except MembersExistException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "MEMBERS_EXIST", "message": str(e)},
        )
    except MembersIneligibleException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MEMBERS_INELIGIBLE",
                "message": str(e),
                "membernames": e.membernames,
            },
        )


@router.delete("/by_id/{group_id}", response_model=GroupResponse)
async def delete_group(
    request: Request,
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Soft delete a group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    group = await group_service.soft_delete_group(group_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.SOFT_DELETE_GROUP,
        resource_type="group",
        resource_id=str(group_id),
        ip_address=get_client_ip(request),
    )

    return await group_service.to_response(group)


@router.post("/by_id/{group_id}/restore", response_model=GroupResponse)
async def restore_group(
    request: Request,
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Restore a soft-deleted group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    group = await group_service.restore_group(group_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.RESTORE_GROUP,
        resource_type="group",
        resource_id=str(group.id),
        ip_address=get_client_ip(request),
    )

    return await group_service.to_response(group, len(group.members))


@router.delete("/by_id/{group_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_group(
    request: Request,
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Hard delete a group (super admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    group_name = await group_service.hard_delete_group(group_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.WIPEOUT_GROUP,
        resource_type="group",
        resource_id=str(group_id),
        details={"name": group_name},
        ip_address=get_client_ip(request),
    )


@router.get("/by_id/{group_id}/members", response_model=list[GroupMemberInfo])
async def list_group_members(
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    sort_by: Annotated[str | None, Query(alias="sortBy")] = None,
    descending: bool = False,
):
    """List members of a group (admin/coach only)."""
    group_service = GroupService(db)
    members = await group_service.list_group_members(
        group_id,
        sort_by=sort_by,
        descending=descending,
    )
    group = await group_service.get_group(group_id)

    return [
        GroupMemberInfo(
            membername=m.membername,
            first_name=m.user.first_name if m.user else None,
            last_name=m.user.last_name if m.user else None,
            nickname=m.user.nickname if m.user else None,
            eligible=is_member_eligible(group, m.user),
        )
        for m in members
    ]


@router.get(
    "/by_id/{group_id}/eligible",
    response_model=list[EligibleUserInfo],
)
async def list_eligible_users(
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Users who could be added to (or request to join) this group."""
    group_service = GroupService(db)
    try:
        users = await group_service.list_eligible_users(group_id)
        return [
            EligibleUserInfo(
                username=u.username,
                first_name=u.first_name,
                last_name=u.last_name,
                nickname=u.nickname,
            )
            for u in users
        ]
    except AutoGroupNotJoinableException:
        raise _auto_not_joinable(group_id)


@router.post(
    "/by_id/{group_id}/members/byname/{membername}",
    response_model=GroupMemberInfo,
    status_code=status.HTTP_201_CREATED,
)
async def add_group_member(
    request: Request,
    group_id: int,
    membername: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Add a member to a group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    try:
        _, user, group_name = await group_service.add_group_member(
            group_id, membername, actor_username=current_user.username
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ADD_GROUP_MEMBER,
            target_username=membername,
            resource_type="group",
            resource_id=str(group_id),
            details={"group_name": group_name},
            ip_address=get_client_ip(request),
        )

        return GroupMemberInfo(
            membername=membername,
            first_name=user.first_name,
            last_name=user.last_name,
            nickname=user.nickname,
        )
    except AutoGroupModificationException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AUTO_GROUP_MODIFICATION_NOT_ALLOWED",
                "message": "Cannot manually modify members of an auto group",
            },
        )
    except NotEligibleException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "NOT_ELIGIBLE", "message": str(e)},
        )
    except AlreadyMemberException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_MEMBER",
                "message": "User is already a member of this group",
            },
        )


@router.post(
    "/by_id/{group_id}/members/bulk",
    response_model=BulkMembersResult,
    status_code=status.HTTP_200_OK,
)
async def add_group_members_bulk(
    request: Request,
    group_id: int,
    data: BulkMembersAdd,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Add multiple members to a group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    try:
        (
            added,
            already_members,
            not_found,
            not_eligible,
            group_name,
        ) = await group_service.add_members_bulk(
            group_id, data.membernames, actor_username=current_user.username
        )

        if added or not_eligible:
            await audit_service.log(
                actor_username=current_user.username,
                action=AuditAction.ADD_GROUP_MEMBERS_BULK,
                resource_type="group",
                resource_id=str(group_id),
                details={
                    "group_name": group_name,
                    "added": added,
                    "already_members": already_members,
                    "not_found": not_found,
                    "not_eligible": not_eligible,
                },
                ip_address=get_client_ip(request),
            )

        return BulkMembersResult(
            added=added,
            already_members=already_members,
            not_found=not_found,
            not_eligible=not_eligible,
        )
    except AutoGroupModificationException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AUTO_GROUP_MODIFICATION_NOT_ALLOWED",
                "message": "Cannot manually modify members of an auto group",
            },
        )


@router.delete("/by_id/{group_id}/members/{membername}", response_model=GroupResponse)
async def remove_group_member(
    request: Request,
    group_id: int,
    membername: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Remove a member from a group (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    try:
        group = await group_service.remove_group_member(group_id, membername)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.REMOVE_GROUP_MEMBER,
            target_username=membername,
            resource_type="group",
            resource_id=str(group_id),
            details={"group_name": group.name},
            ip_address=get_client_ip(request),
        )
    except AutoGroupModificationException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AUTO_GROUP_MODIFICATION_NOT_ALLOWED",
                "message": "Cannot manually modify members of an auto group",
            },
        )

    return await group_service.to_response(group, len(group.members))


# =============================================================================
# Join requests (admin/coach view + admin decisions)
# =============================================================================


@router.get(
    "/by_id/{group_id}/requests",
    response_model=list[JoinRequestResponse],
)
async def list_join_requests(
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    """List join requests for a group (admin/coach)."""
    group_service = GroupService(db)
    requests = await group_service.list_group_requests(group_id, status_filter)
    return [JoinRequestResponse.from_model(r) for r in requests]


@router.post(
    "/by_id/{group_id}/requests/{request_id}/approve",
    response_model=JoinRequestResponse,
)
async def approve_join_request(
    request: Request,
    group_id: int,
    request_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Approve a pending join request (admin only). Re-checks eligibility."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    try:
        req, _added = await group_service.approve_request(
            group_id, request_id, actor_username=current_user.username
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.APPROVE_GROUP_JOIN_REQUEST,
            target_username=req.username,
            resource_type="group_join_request",
            resource_id=str(request_id),
            details={"group_id": group_id},
            ip_address=get_client_ip(request),
        )
        return JoinRequestResponse.from_model(req)
    except JoinRequestNotPendingException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOIN_REQUEST_NOT_PENDING",
                "message": str(e),
            },
        )
    except AutoGroupNotJoinableException:
        raise _auto_not_joinable(group_id)
    except NotEligibleException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "NOT_ELIGIBLE", "message": str(e)},
        )


@router.post(
    "/by_id/{group_id}/requests/{request_id}/reject",
    response_model=JoinRequestResponse,
)
async def reject_join_request(
    request: Request,
    group_id: int,
    request_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    body: JoinRequestRejectBody | None = None,
):
    """Reject a pending join request (admin only)."""
    group_service = GroupService(db)
    audit_service = AuditService(db)

    reason = body.reason if body is not None else None

    try:
        req = await group_service.reject_request(
            group_id, request_id, actor_username=current_user.username, reason=reason
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.REJECT_GROUP_JOIN_REQUEST,
            target_username=req.username,
            resource_type="group_join_request",
            resource_id=str(request_id),
            details={"group_id": group_id, "reason": reason},
            ip_address=get_client_ip(request),
        )
        return JoinRequestResponse.from_model(req)
    except JoinRequestNotPendingException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOIN_REQUEST_NOT_PENDING",
                "message": str(e),
            },
        )
