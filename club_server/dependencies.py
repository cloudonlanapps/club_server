from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db.engine import get_async_session
from .db.models.user import User, UserStatus, Role
from .schemas.common import UserRoles
from .services.auth import AuthService
from .services.auth_session import AuthSessionService

if TYPE_CHECKING:
    from .db.models.event import Event


def get_user_roles(user: User) -> set[str]:
    """Parse roles from JSON column."""
    if not user.roles:
        return set()
    roles_data = UserRoles.model_validate_json(user.roles)
    return set(roles_data.roles)


security = HTTPBearer()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session dependency.

    Always inject this with ``Depends(get_db, scope="function")``. The session
    commit happens in this generator's teardown; ``function`` scope makes FastAPI
    run that teardown before the response is sent, so a follow-up request sees the
    just-written row. The default ``request`` scope commits after the response,
    which races (#212).
    """
    async for session in get_async_session():
        yield session


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    """Get the current authenticated user."""
    token = credentials.credentials
    auth_service = AuthService(db)

    payload = auth_service.decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Invalid or expired token"},
        )

    username = payload.sub

    user = await auth_service.get_user_by_username(username)
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
            detail={"code": "INVALID_TOKEN", "message": "Invalid or expired token"},
        )

    if user.status == UserStatus.blocked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ACCOUNT_BLOCKED", "message": "Account is blocked"},
        )

    if user.status == UserStatus.left:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ACCOUNT_LEFT", "message": "Account has left"},
        )

    return user


async def get_authenticated_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Authenticated, not-banned user. Accepts ``registered``, ``pending``,
    ``active``. ``blocked`` and ``left`` are already rejected upstream by
    ``get_current_user``."""
    return current_user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current active user (not blocked, not pending)."""
    if current_user.status != UserStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCOUNT_NOT_ACTIVE", "message": "Account is not active"},
        )
    return current_user


def require_role(*roles: Role):
    """Dependency factory for requiring specific roles."""

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        # Super admin bypasses role checks
        if current_user.is_super_admin:
            return current_user

        user_roles = get_user_roles(current_user)
        if not any(role.value in user_roles for role in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INSUFFICIENT_PERMISSION",
                    "message": f"Requires one of roles: {[r.value for r in roles]}",
                },
            )
        return current_user

    return role_checker


def require_admin():
    """Dependency for requiring admin role."""
    return require_role(Role.admin)


def require_admin_or_coach():
    """Dependency for requiring admin or coach role."""
    return require_role(Role.admin, Role.coach)


def is_coach(user: User) -> bool:
    """True if the user holds the coach role. Super admin does not imply it."""
    return Role.coach.value in get_user_roles(user)


def require_coach():
    """Dependency for requiring the coach role itself.

    Unlike ``require_role``, the super admin is not let through: writing an
    evaluation is a coach's act, and an admin evaluates no one
    (`evaluation_requirements.md` R34, R45).
    """

    async def coach_checker(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if not is_coach(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INSUFFICIENT_PERMISSION",
                    "message": "Requires the coach role",
                },
            )
        return current_user

    return coach_checker


def require_super_admin():
    """Dependency for requiring super admin."""

    async def super_admin_checker(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if not current_user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INSUFFICIENT_PERMISSION",
                    "message": "Requires super admin privileges",
                },
            )
        return current_user

    return super_admin_checker


# ---------------------------------------------------------------------------
# Plain permission helpers (not dependencies)
#
# Called inside route handlers, typically after a resource has been loaded
# from the DB (so they cannot be modelled as pure ``Depends()``). Routers
# should prefer these over re-implementing role/identity checks inline.
# ---------------------------------------------------------------------------


def require_credit_system_enabled() -> None:
    """Refuse every credit operation where the deployment does not run on credits.

    Attached to the credit routers rather than checked per handler, so the
    refusal happens at the edge and no credit table is touched (#294, R94a).
    The endpoints stay registered on every deployment so that the published
    API surface does not vary with configuration — only the answer does.
    """
    if not settings.credit_system_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "CREDIT_SYSTEM_DISABLED",
                "message": "The credit system is not enabled on this deployment",
            },
        )


def require_event_marketing_enabled() -> None:
    """Refuse every event-marketing operation where the module is not shipped (#410, R5)."""
    if not settings.event_marketing_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EVENT_MARKETING_DISABLED",
                "message": "Event marketing is not enabled on this deployment",
            },
        )


def require_evaluations_enabled() -> None:
    """Refuse every evaluation operation where the deployment does not ship them.

    Attached to the evaluation routers rather than checked per handler, so the
    refusal happens at the edge and no evaluation table is touched (#302, R61).
    The endpoints stay registered on every deployment so that the published
    API surface does not vary with configuration — only the answer does,
    exactly as the credit system does above.
    """
    if not settings.evaluations_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EVALUATIONS_DISABLED",
                "message": "Evaluations are not enabled on this deployment",
            },
        )


def is_admin(user: User) -> bool:
    """True if the user is super-admin or has the admin role."""
    if user.is_super_admin:
        return True
    return Role.admin.value in get_user_roles(user)


def is_admin_or_coach(user: User) -> bool:
    """True if the user is super-admin or has the admin/coach role."""
    if user.is_super_admin:
        return True
    roles = get_user_roles(user)
    return Role.admin.value in roles or Role.coach.value in roles


def _forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "INSUFFICIENT_PERMISSION", "message": message},
    )


def check_organizer_or_admin(organizer_name: str | None, user: User) -> bool:
    """True if the user is admin or the named organizer of a resource."""
    if is_admin(user):
        return True
    if organizer_name is not None and organizer_name == user.username:
        return True
    return False


def require_organizer_or_admin(organizer_name: str | None, user: User) -> None:
    """Raise 403 unless the user is admin or the named organizer.

    Plain helper because it needs the loaded resource — cannot be a
    ``Depends()`` since the organizer name comes from a DB row.
    """
    if check_organizer_or_admin(organizer_name, user):
        return
    raise _forbidden("Only admin or event organizer can perform this action")


def check_event_coach_or_organizer_or_admin(event: "Event", user: User) -> bool:
    """True if the user is admin, the event's organizer, or one of its assigned coaches.

    The attendance tier (#247): "assigned" is read from the current
    schedule's coach rows (#386), never from a global role.
    """
    if check_organizer_or_admin(event.organizer_name, user):
        return True
    return user.username in event.coach_names_list


def require_event_coach_or_organizer_or_admin(event: "Event", user: User) -> None:
    """Raise 403 unless the user is admin, the organizer or an assigned coach.

    Applied to the attendance endpoints only: marking, clearing and leave
    decisions. Enrollment, editing and credit stay organizer-or-admin.
    """
    if check_event_coach_or_organizer_or_admin(event, user):
        return
    raise _forbidden(
        "Only admin, the event organizer or an assigned coach can perform this action"
    )


def require_self_or_coach(target_username: str, current_user: User) -> None:
    """Raise 403 unless the request targets the caller or the caller is a coach.

    For a member's published evaluations, which an admin does not read
    (`evaluation_requirements.md` R42).
    """
    if current_user.username == target_username or is_coach(current_user):
        return
    raise _forbidden("You do not have permission to access this user's data")


def require_self_or_staff(target_username: str, current_user: User) -> None:
    """Raise 403 unless the request targets the caller or the caller is staff.

    "Staff" = super-admin, admin, or coach. Use for **read** endpoints that
    expose user-owned data.
    """
    if current_user.username == target_username:
        return
    if is_admin_or_coach(current_user):
        return
    raise _forbidden("You do not have permission to access this user's data")


def require_self_or_event_staff(
    target_username: str, event: "Event", current_user: User
) -> None:
    """Raise 403 unless the caller is the member, an admin, or the event's staff.

    For the self-service enrollment and leave **mutations** on a member's
    behalf: a coach may act only on an event they organize or are assigned to
    (#460). Reads stay on ``require_self_or_staff``.
    """
    if current_user.username == target_username:
        return
    if check_event_coach_or_organizer_or_admin(event, current_user):
        return
    raise _forbidden(
        "Only the member, an admin, or the event's organizer or coaches can act "
        "for this member"
    )


def require_self_or_admin(target_username: str, current_user: User) -> None:
    """Raise 403 unless the request targets the caller or the caller is admin.

    Use for **mutate** endpoints where coach-level access is not allowed.
    """
    if current_user.username == target_username:
        return
    if is_admin(current_user):
        return
    raise _forbidden("You do not have permission to modify this user's data")
