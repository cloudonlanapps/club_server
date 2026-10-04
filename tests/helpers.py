"""Test helper functions."""

import json
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.media import Media
from club_server.db.models.media_links import UserMediaLink
from club_server.db.models.user import User, UserStatus
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms


async def attach_identity_document(
    db_session: AsyncSession,
    username: str,
    *,
    tag: str = "identity_document",
) -> str:
    """Insert a Media row + UserMediaLink row tagged ``identity_document``
    for ``username`` so the user satisfies the precondition enforced by
    ``submit-for-review`` (#142). Returns the media UUID.

    Bypasses the upload pipeline; the on-disk file is not created. Tests
    that exercise the upload endpoints should use those instead.
    """
    now = now_utc_ms()
    media_uuid = str(_uuid.uuid4())
    db_session.add(
        Media(
            uuid=media_uuid,
            original_filename=f"{username}-identity.jpg",
            media_type="image",
            mime_type="image/jpeg",
            original_mime_type="image/jpeg",
            original_extension="jpg",
            file_size=1024,
            preserve_original=0,
            conversion_status="completed",
            uploaded_by=username,
            access_roles='["self", "admin"]',
            is_encrypted=False,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()
    db_session.add(
        UserMediaLink(
            username=username,
            media_uuid=media_uuid,
            tag=tag,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()
    return media_uuid


async def create_admin_user(db_session: AsyncSession) -> str:
    """Create an admin super user and return an access token.

    This replaces the HTTP bootstrap endpoint for tests.
    """
    user = User(
        username="admin",
        password=AuthService.hash_password("adminpass123"),
        first_name="Admin",
        status=UserStatus.active.value,
        is_super_admin=1,
        roles=json.dumps({"roles": ["admin"]}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token("admin", is_super_admin=True)


async def create_regular_admin_user(
    db_session: AsyncSession, username: str = "regular_admin"
) -> str:
    """Create a regular admin user (not super admin) and return an access token."""
    user = User(
        username=username,
        password=AuthService.hash_password("adminpass123"),
        first_name="Regular Admin",
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": ["admin"]}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token(username, is_super_admin=False)


async def create_registered_user(
    db_session: AsyncSession, username: str = "newbie"
) -> str:
    """Create a freshly-registered user (status=registered, has not yet
    submitted for admin review) and return an access token. Mirrors the
    shape of ``create_admin_user`` etc."""
    user = User(
        username=username,
        password=AuthService.hash_password("regpass123"),
        first_name=username.capitalize(),
        status=UserStatus.registered.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token(username, is_super_admin=False)


async def create_coach_user(db_session: AsyncSession, username: str = "coach") -> str:
    """Create a coach user and return an access token."""
    user = User(
        username=username,
        password=AuthService.hash_password("coachpass123"),
        first_name="Coach",
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": ["coach"]}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token(username, is_super_admin=False)


async def create_coach_admin_user(
    db_session: AsyncSession, username: str = "coachadmin"
) -> str:
    """Create a user holding both the admin and coach roles; return a token."""
    user = User(
        username=username,
        password=AuthService.hash_password("coachpass123"),
        first_name="Coach Admin",
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": ["admin", "coach"]}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token(username, is_super_admin=False)


async def create_member_user(
    db_session: AsyncSession,
    username: str = "member",
    *,
    date_of_birth: int | None = None,
    gender: str | None = None,
) -> str:
    """Create a plain member (no admin/coach roles) and return an access token."""
    user = User(
        username=username,
        password=AuthService.hash_password("memberpass123"),
        first_name=username.capitalize(),
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        date_of_birth=date_of_birth,
        gender=gender,
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token(username, is_super_admin=False)


async def create_user_with_status(
    db_session: AsyncSession,
    username: str,
    status: UserStatus,
    *,
    deleted: bool = False,
    is_super_admin: bool = False,
) -> str:
    """Create a user in an arbitrary status and return an access token.

    Covers the statuses the role-specific helpers above do not reach
    (``blocked``, ``left``) and the soft-deleted case, both of which the
    user counts must account for.
    """
    now = now_utc_ms()
    user = User(
        username=username,
        password=AuthService.hash_password("statuspass123"),
        first_name=username.capitalize(),
        status=status.value,
        is_super_admin=1 if is_super_admin else 0,
        roles=json.dumps({"roles": []}),
        created_at=now,
        deleted_at=now if deleted else None,
    )
    db_session.add(user)
    await db_session.flush()
    return AuthService.create_access_token(username, is_super_admin=is_super_admin)


async def create_media_row(
    db_session: AsyncSession,
    *,
    uploaded_by: str,
    public: bool,
    media_type: str = "image",
) -> str:
    """Insert a completed media row and return its uuid.

    ``public`` picks the access roles: ``["public"]`` when true, otherwise a
    roster only staff can view, so tests can assert the anonymous gate.
    """
    import uuid as _uuid

    from club_server.db.models.media import Media

    now = now_utc_ms()
    media_uuid = str(_uuid.uuid4())
    db_session.add(
        Media(
            uuid=media_uuid,
            original_filename=f"{media_uuid}.jpg",
            media_type=media_type,
            mime_type="image/jpeg" if media_type == "image" else "video/mp4",
            original_mime_type="image/jpeg" if media_type == "image" else "video/mp4",
            original_extension="jpg" if media_type == "image" else "mp4",
            file_size=1024,
            preserve_original=0,
            conversion_status="completed",
            uploaded_by=uploaded_by,
            access_roles='["public"]' if public else '["admin", "coach"]',
            is_encrypted=False,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()
    return media_uuid
