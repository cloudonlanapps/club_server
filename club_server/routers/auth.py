from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..utils import duplicate_user_error_detail, get_client_ip
from ..validation import validate_required_profile_fields
from ..db.models.user import User, UserStatus
from ..dependencies import get_authenticated_user, get_current_user, get_db, security
from ..schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    RefreshTokenResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UsernameAvailableResponse,
)
from ..schemas.user import UserPrivateResponse
from ..services.auth import AuthService, new_session_id
from ..services.auth_session import AuthSessionService
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.user_review import UserReviewService

router = APIRouter(prefix="/auth", tags=["Authentication"])


def now_ms() -> int:
    """Get current time in milliseconds since epoch."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@router.get("/username-available", response_model=UsernameAvailableResponse)
async def username_available(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    username: Annotated[str, Query(min_length=1, max_length=50)],
):
    """Check whether a username is available for registration.

    Public endpoint used by the registration form for inline validation.
    Uses the same uniqueness check as `POST /auth/register`; the DB primary
    key on `User.username` remains the source of truth at registration time.
    """
    auth_service = AuthService(db)
    taken = await auth_service.is_username_taken(username)
    return UsernameAvailableResponse(username=username, available=not taken)


@router.post(
    "/register", response_model=UserPrivateResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    request: Request,
    data: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Register a new user account.

    With identity verification on (the default) the user starts
    ``registered`` and is put before admins by submit-for-review (#142).
    With it off there is no document to wait for, so the user starts
    ``pending`` and admins are notified now (#428).
    """
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    validate_required_profile_fields(
        first_name=data.first_name,
        last_name=data.last_name,
        gender=data.gender,
        date_of_birth_utc=data.date_of_birth_utc,
        phone=data.phone,
    )

    # Create the user: registered, or pending when verification is off (#428)
    try:
        user = await auth_service.create_user(
            username=data.username,
            password=data.password,
            email=data.email,
            first_name=data.first_name,
            middle_name=data.middle_name,
            last_name=data.last_name,
            phone=data.phone,
            date_of_birth=data.date_of_birth_utc,
            gender=data.gender.value if data.gender else None,
            address=data.address.model_dump_json() if data.address else None,
            status=(
                UserStatus.registered
                if settings.identity_verification_required
                else UserStatus.pending
            ),
        )
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_user_error_detail(e),
        )
    if not settings.identity_verification_required:
        await auth_service.notify_registration_pending(user)

    # Audit log
    await audit_service.log(
        actor_username=data.username,
        action=AuditAction.REGISTER,
        ip_address=get_client_ip(request),
    )

    return UserPrivateResponse.from_model(user)


@router.post("/login", response_model=RefreshTokenResponse)
async def login(
    request: Request,
    data: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Login with username and password."""
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    user = await auth_service.authenticate_user(data.username, data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Invalid username or password",
            },
        )

    if user.status == "blocked":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ACCOUNT_BLOCKED", "message": "Account is blocked"},
        )

    if user.status == UserStatus.left:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ACCOUNT_LEFT", "message": "Account has left"},
        )

    # `registered` and `pending` users can log in (parity behavior). They
    # get a token so they can view their own profile, manage onboarding
    # documents, call submit-for-review, and reapply after a reconsider.
    # Endpoints that require an approved account use `get_current_active_user`,
    # which rejects non-active statuses with 403 ACCOUNT_NOT_ACTIVE.

    # Create tokens: login begins a session they both belong to (#510)
    session_id = new_session_id()
    access_token = auth_service.create_access_token(
        user.username, bool(user.is_super_admin), session_id
    )
    refresh_token = auth_service.create_refresh_token(
        user.username, bool(user.is_super_admin), session_id
    )

    # Update last login
    user.last_login_at = now_ms()
    await db.flush()

    # Audit log
    await audit_service.log(
        actor_username=user.username,
        action=AuditAction.LOGIN,
        ip_address=get_client_ip(request),
    )

    return RefreshTokenResponse(
        access_token=access_token,
        expires_at_utc=int(
            (
                datetime.now(timezone.utc)
                + timedelta(minutes=settings.access_token_expire_minutes)
            ).timestamp()
            * 1000
        ),
        refresh_token=refresh_token,
    )


@router.get("/me", response_model=UserPrivateResponse)
async def get_current_user_info(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Get the current authenticated user's profile."""
    note = await UserReviewService(db).get_active_review_note(current_user)
    return UserPrivateResponse.from_model(current_user, admin_review_note=note)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """End the session the presented access token belongs to (#510).

    That token, the refresh token issued with it and every pair refreshed
    from them are refused afterwards (401). The user's other sessions keep
    working. Any logged-in user may log out, whatever their status.
    """
    payload = AuthService.decode_token(credentials.credentials)
    if payload is not None:
        await AuthSessionService(db).revoke(payload)

    audit_service = AuditService(db)
    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.LOGOUT,
        ip_address=get_client_ip(request),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: Request,
    data: ChangePasswordRequest,
    current_user: Annotated[User, Depends(get_authenticated_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Change the caller's own password, whatever their status (#510)."""
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    if not AuthService.verify_password(data.current_password, current_user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Current password is incorrect",
            },
        )

    success = await auth_service.change_password(
        current_user.username,
        data.new_password,
        actor_username=current_user.username,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "PASSWORD_CHANGE_FAILED",
                "message": "Failed to change password",
            },
        )

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.PASSWORD_CHANGED,
        ip_address=get_client_ip(request),
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    request: Request,
    data: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Request password reset via email.

    If the email exists, a password reset link will be sent.
    For security, always returns success even if email not found.
    """
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    outcome = await auth_service.initiate_password_reset(data.email)

    # Record the real outcome server-side only. The HTTP response stays uniform
    # (204, no body) so the caller cannot tell whether the account exists.
    await audit_service.log(
        actor_username="system",
        action=AuditAction.PASSWORD_RESET_REQUESTED,
        details={
            "email": data.email,
            "accountExists": outcome.account_exists,
            "emailSent": outcome.email_sent,
        },
        ip_address=get_client_ip(request),
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(
    request: Request,
    data: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Refresh access token using refresh token."""
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    payload = auth_service.decode_refresh_token(data.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_REFRESH_TOKEN",
                "message": "Invalid or expired refresh token",
            },
        )

    user = await auth_service.get_user_by_username(payload.sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )

    if AuthService.issued_before_password_change(
        payload, user
    ) or await AuthSessionService(db).is_revoked(payload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_REFRESH_TOKEN",
                "message": "Invalid or expired refresh token",
            },
        )

    if user.status == "blocked":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ACCOUNT_BLOCKED", "message": "Account is blocked"},
        )

    if user.status == UserStatus.left:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ACCOUNT_LEFT", "message": "Account has left"},
        )

    # The new pair stays in the session it was refreshed from (#510)
    session_id = payload.sid or new_session_id()
    access_token = auth_service.create_access_token(
        user.username, bool(user.is_super_admin), session_id
    )
    new_refresh_token = auth_service.create_refresh_token(
        user.username, bool(user.is_super_admin), session_id
    )

    await audit_service.log(
        actor_username=user.username,
        action=AuditAction.TOKEN_REFRESHED,
        ip_address=get_client_ip(request),
    )

    return RefreshTokenResponse(
        access_token=access_token,
        expires_at_utc=int(
            (
                datetime.now(timezone.utc)
                + timedelta(minutes=settings.access_token_expire_minutes)
            ).timestamp()
            * 1000
        ),
        refresh_token=new_refresh_token,
    )
