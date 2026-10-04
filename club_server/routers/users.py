from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User, UserStatus, Role
from ..mailer import EmailMessage, get_transactional_sender
from ..mailer import templates as mailer_templates
from ..mailer.config import email_settings
from ..utils import duplicate_user_error_detail, get_client_ip
from ..validation import validate_required_profile_fields
from ..dependencies import (
    get_authenticated_user,
    get_current_active_user,
    get_db,
    require_admin,
    require_admin_or_coach,
    require_self_or_admin,
    require_self_or_staff,
    require_super_admin,
)
from ..schemas.user import (
    ReapplyRequest,
    ReconsiderRequest,
    ResolutionReasonBody,
    RoleAssign,
    UserAdminCreate,
    UserCountResponse,
    UserInfoResponse,
    UserPrivateResponse,
    UserUpdate,
)
from ..schemas.common import PaginatedResponse
from ..exceptions import (
    CannotBlockSuperAdminException,
    CannotTransferFromNonSuperAdminException,
    CannotTransferToSelfException,
    IdentityDocumentRequiredException,
    InvalidStateException,
    RoleAlreadyAssignedException,
    RoleNotFoundException,
    UserAlreadyDeletedException,
    UserNotActiveException,
)
from ..schemas.group import GroupResponse
from ..services.audit import AuditService
from ..services.club_info import ClubInfoService
from ..services.staff_listing import StaffListingService
from ..services.audit_actions import AuditAction
from ..services.group import GroupService
from ..services.user import UserService
from ..services.user_review import (
    CannotReconsiderSelfException,
    NoActiveReviewRequestException,
    UserReviewService,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=PaginatedResponse[UserInfoResponse])
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    status_filter: Annotated[UserStatus | None, Query(alias="status")] = None,
    role: Annotated[str | None, Query()] = None,
    min_age: Annotated[int | None, Query(alias="minAge", ge=0)] = None,
    max_age: Annotated[int | None, Query(alias="maxAge", ge=0)] = None,
    search_term: Annotated[
        str | None, Query(alias="searchTerm", max_length=100)
    ] = None,
    sort_by: Annotated[str | None, Query(alias="sortBy")] = None,
    descending: Annotated[bool, Query()] = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List all users (admin only)."""
    user_service = UserService(db)

    return await user_service.list_users(
        status_filter=status_filter,
        role=role,
        min_age=min_age,
        max_age=max_age,
        search_term=search_term,
        sort_by=sort_by,
        descending=descending,
        offset=offset,
        limit=limit,
    )


@router.get("/count", response_model=UserCountResponse)
async def count_users(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Count users by status (admin or coach only)."""
    user_service = UserService(db)

    by_status = await user_service.count_users_by_status()
    return UserCountResponse(by_status=by_status, total=sum(by_status.values()))


@router.get("/deleted", response_model=PaginatedResponse[UserInfoResponse])
async def list_deleted_users(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    search_term: Annotated[
        str | None, Query(alias="searchTerm", max_length=100)
    ] = None,
    sort_by: Annotated[str | None, Query(alias="sortBy")] = None,
    descending: Annotated[bool, Query()] = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List soft-deleted users (admin only)."""
    user_service = UserService(db)
    return await user_service.list_deleted_users(
        search_term=search_term,
        sort_by=sort_by,
        descending=descending,
        offset=offset,
        limit=limit,
    )


@router.post("/me/submit-for-review", response_model=UserPrivateResponse)
async def submit_for_review(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Signal that the caller has finished setting up their account and is
    ready for admin review (#120).

    Flips caller status from ``registered`` to ``pending`` atomically and
    enqueues the ``user_approval`` admin notification deferred from
    registration. Returns 409 ``INVALID_STATE`` for any other starting
    status."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.submit_for_review(current_user.username)
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except IdentityDocumentRequiredException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "IDENTITY_DOCUMENT_REQUIRED", "message": str(e)},
        )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.SUBMIT_FOR_REVIEW,
        ip_address=get_client_ip(request),
    )

    return UserPrivateResponse.from_model(user)


@router.get("/by_id/{username}", response_model=UserInfoResponse)
async def get_user(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get user public profile."""
    user_service = UserService(db)

    user = await user_service.get_user(username)
    return UserInfoResponse.from_model(user)


@router.get("/by_id/{username}/private", response_model=UserPrivateResponse)
async def get_user_private(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get user private profile. Users can view their own; admins and coaches can view any."""
    require_self_or_staff(username, current_user)

    user_service = UserService(db)

    user = await user_service.get_user(username)
    note = await UserReviewService(db).get_active_review_note(user)
    return UserPrivateResponse.from_model(user, admin_review_note=note)


@router.get("/by_id/{username}/groups", response_model=list[GroupResponse])
async def get_user_groups(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get groups a user belongs to. Users can view their own; admins/coaches can view any."""
    require_self_or_staff(username, current_user)

    group_service = GroupService(db)

    return await group_service.get_user_groups(username)


@router.patch("/by_id/{username}", response_model=UserPrivateResponse)
async def update_user(
    request: Request,
    username: str,
    data: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Update user profile. Users can update their own profile; admins can update any profile."""
    require_self_or_admin(username, current_user)

    user_service = UserService(db)
    audit_service = AuditService(db)

    if not current_user.is_super_admin:
        # An admin who could redirect the super admin's email would own the
        # account through the public password reset (#458).
        target = await user_service.get_user_or_raise(username)
        if target.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "SUPER_ADMIN_PROTECTION",
                    "message": "Only a super admin can edit a super admin's profile",
                },
            )

        protected_schema_fields = ("gender", "date_of_birth_utc")
        rejected = [f for f in protected_schema_fields if f in data.model_fields_set]
        if rejected:
            field_name_map = {"date_of_birth_utc": "dateOfBirthUtc", "gender": "gender"}
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PROTECTED_FIELDS",
                    "message": "These fields can only be edited by a super admin",
                    "fields": [field_name_map[f] for f in rejected],
                },
            )

    try:
        user = await user_service.update_user(
            username,
            actor_username=current_user.username,
            **data.to_service_kwargs(),
        )
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_user_error_detail(e),
        )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.UPDATE_USER,
        target_username=username,
        ip_address=get_client_ip(request),
    )

    return UserPrivateResponse.from_model(user)


@router.post("/by_id/{username}/approve", response_model=UserInfoResponse)
async def approve_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    body: ResolutionReasonBody | None = None,
):
    """Approve a pending user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.approve_user(
            username,
            actor_username=current_user.username,
            resolution_reason=body.resolution_reason if body else None,
        )

        # Notify the approved user by email (best-effort; never fails approval).
        email_sent = False
        if user.email:
            club_name, club_short_name = await ClubInfoService(db).branding()
            subject, html, text = mailer_templates.account_approved(
                club_name=club_name,
                club_short_name=club_short_name,
                first_name=user.first_name,
                login_url=email_settings.login_url,
            )
            result = await get_transactional_sender().send(
                EmailMessage(to=user.email, subject=subject, html=html, text=text)
            )
            email_sent = result.success

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.APPROVE_USER,
            target_username=username,
            details={"emailSent": email_sent},
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_STATE",
                "message": e.message,
            },
        )


@router.post("/by_id/{username}/block", response_model=UserInfoResponse)
async def block_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    body: ResolutionReasonBody | None = None,
):
    """Block a user (admin only). Cannot block super admin."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.block_user(
            username,
            actor_username=current_user.username,
            resolution_reason=body.resolution_reason if body else None,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.BLOCK_USER,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except CannotBlockSuperAdminException:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPER_ADMIN_PROTECTION",
                "message": "Cannot block super admin",
            },
        )
    except InvalidStateException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "ALREADY_BLOCKED", "message": "User is already blocked"},
        )


@router.post(
    "/by_id/{username}/reconsider",
    response_model=UserInfoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reconsider_user(
    request: Request,
    username: str,
    data: ReconsiderRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Ask a pending user to revisit their registration (admin only, #122).

    Flips the target back to ``registered`` so the deferred
    ``user_approval`` admin notification disappears until they
    resubmit. Supersedes any prior active row.
    """
    review_service = UserReviewService(db)
    audit_service = AuditService(db)

    try:
        _ = await review_service.reconsider(
            target_username=username,
            actor_username=current_user.username,
            reason=data.reason,
        )
    except CannotReconsiderSelfException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANNOT_RECONSIDER_SELF",
                "message": "Cannot reconsider self",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.RECONSIDER_USER,
        target_username=username,
        details={"reason": data.reason},
        ip_address=get_client_ip(request),
    )

    user_service = UserService(db)
    user = await user_service.get_user(username)
    return UserInfoResponse.from_model(user)


@router.patch("/by_id/{username}/reapply", response_model=UserPrivateResponse)
async def reapply_user(
    request: Request,
    username: str,
    data: ReapplyRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Self-only resubmit of registration fields in response to an
    active reconsider request (#122). Caller must subsequently call
    ``POST /v1/users/me/submit-for-review`` to re-notify admins."""
    if username != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INSUFFICIENT_PERMISSION",
                "message": "Cannot reapply for another user",
            },
        )

    review_service = UserReviewService(db)
    audit_service = AuditService(db)

    try:
        user, _ = await review_service.reapply(username, data.to_update_fields())
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_user_error_detail(e),
        )
    except NoActiveReviewRequestException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_ACTIVE_REVIEW_REQUEST",
                "message": "No active review request for user",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.REAPPLY,
        ip_address=get_client_ip(request),
    )

    return UserPrivateResponse.from_model(user)


@router.post("/by_id/{username}/unblock", response_model=UserInfoResponse)
async def unblock_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Unblock a user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.unblock_user(username)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.UNBLOCK_USER,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "NOT_BLOCKED",
                "message": e.message,
            },
        )


@router.post("/by_id/{username}/roles", response_model=UserInfoResponse)
async def assign_role(
    request: Request,
    username: str,
    data: RoleAssign,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Assign a role to a user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.assign_role(
            username, data.role, actor_username=current_user.username
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ASSIGN_ROLE,
            target_username=username,
            details={"role": data.role.value},
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except RoleAlreadyAssignedException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ROLE_ALREADY_ASSIGNED",
                "message": f"User already has role: {e.role}",
            },
        )


@router.delete("/by_id/{username}/roles/{role}", response_model=UserInfoResponse)
async def remove_role(
    request: Request,
    username: str,
    role: Role,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Remove a role from a user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.remove_role(
            username, role, actor_username=current_user.username
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.REMOVE_ROLE,
            target_username=username,
            details={"role": role.value},
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except RoleNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ROLE_NOT_FOUND",
                "message": f"User does not have role: {e.role}",
            },
        )


@router.post(
    "", response_model=UserPrivateResponse, status_code=status.HTTP_201_CREATED
)
async def create_user(
    request: Request,
    data: UserAdminCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Create a new user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    validate_required_profile_fields(
        first_name=data.first_name,
        last_name=data.last_name,
        gender=data.gender,
        date_of_birth_utc=data.date_of_birth_utc,
        phone=data.phone,
    )

    try:
        user = await user_service.create_user(**data.to_service_kwargs())
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_user_error_detail(e),
        )

    if data.is_guest:
        await StaffListingService(db).mark_guest(user)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.CREATE_USER,
        target_username=data.username,
        details={"isGuest": True} if data.is_guest else None,
        ip_address=get_client_ip(request),
    )

    return UserPrivateResponse.from_model(user)


@router.delete("/by_id/{username}", response_model=UserInfoResponse)
async def delete_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Soft delete a user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.delete_user(username)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.DELETE_USER,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except CannotBlockSuperAdminException:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPER_ADMIN_PROTECTION",
                "message": "Cannot delete super admin",
            },
        )
    except UserAlreadyDeletedException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "ALREADY_DELETED", "message": "User is already deleted"},
        )


@router.delete("/by_id/{username}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Permanently delete a user and all their data (super admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        # Log before delete: the FK reference must exist at insert time,
        # and SET NULL will preserve this record when the user is removed
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.HARD_DELETE_USER,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        await user_service.hard_delete_user(username, actor=current_user.username)
    except CannotBlockSuperAdminException:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPER_ADMIN_PROTECTION",
                "message": "Cannot delete super admin",
            },
        )


@router.post("/by_id/{username}/restore", response_model=UserPrivateResponse)
async def restore_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Restore a soft-deleted user (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    user = await user_service.restore_user(username)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.RESTORE_USER,
        target_username=username,
        ip_address=get_client_ip(request),
    )

    return UserPrivateResponse.from_model(user)


@router.post("/by_id/{username}/mark-left", response_model=UserInfoResponse)
async def mark_user_left(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Mark a user as left (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.mark_left(username)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.MARK_LEFT,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except CannotBlockSuperAdminException:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPER_ADMIN_PROTECTION",
                "message": "Cannot mark super admin as left",
            },
        )
    except InvalidStateException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "ALREADY_LEFT",
                "message": "User is already marked as left",
            },
        )
    except UserNotActiveException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": str(e)},
        )


@router.post("/by_id/{username}/reactivate", response_model=UserInfoResponse)
async def reactivate_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Reactivate a user who left (admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        user = await user_service.reactivate_user(username)

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.REACTIVATE_USER,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(user)
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "NOT_LEFT", "message": e.message},
        )


@router.post("/by_id/{username}/transfer-superadmin", response_model=UserInfoResponse)
async def transfer_superadmin(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Transfer super admin role to another user (super admin only)."""
    user_service = UserService(db)
    audit_service = AuditService(db)

    try:
        new_super_admin = await user_service.transfer_superadmin(
            from_username=current_user.username,
            to_username=username,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.TRANSFER_SUPERADMIN,
            target_username=username,
            ip_address=get_client_ip(request),
        )

        return UserInfoResponse.from_model(new_super_admin)
    except CannotTransferFromNonSuperAdminException:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INSUFFICIENT_PERMISSION",
                "message": "Only super admin can transfer super admin role",
            },
        )
    except CannotTransferToSelfException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANNOT_TRANSFER_TO_SELF",
                "message": "Cannot transfer super admin role to self",
            },
        )
