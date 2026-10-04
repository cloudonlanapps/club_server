"""Tests for the public profile API (#262, #266).

Covers the unauthenticated `/public` profile/staff endpoints (now gated on
the stored, user-owned `is_public_profile` flag), the public-avatar gate, the
new `publicId` / `isPublicProfile` fields on `UserInfoResponse`, and the
self+coach-only update rules with the `use_name_publicly` coupling.
"""

import json
import uuid as _uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.media import Media
from club_server.db.models.media_links import UserMediaLink
from club_server.db.models.user import User, UserStatus
from club_server.services.auth import AuthService
from club_server.utils import generate_public_id, now_utc_ms

from .helpers import create_admin_user, create_member_user


async def _make_coach(
    db: AsyncSession,
    username: str,
    *,
    is_public_profile: bool,
    role: str = "coach",
    status: str = UserStatus.active.value,
    bio: str | None = None,
    achievements: str | None = None,
    use_name_publicly: bool = True,
    first_name: str | None = "Coach",
) -> None:
    """Create a user with the given public-profile-relevant fields."""
    db.add(
        User(
            username=username,
            password=AuthService.hash_password("coachpass123"),
            first_name=first_name,
            use_name_publicly=1 if use_name_publicly else 0,
            is_public_profile=1 if is_public_profile else 0,
            bio=bio,
            achievements=achievements,
            status=status,
            is_super_admin=0,
            roles=json.dumps({"roles": [role]} if role else {"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await db.flush()


async def _attach_avatar(db: AsyncSession, username: str, *, public: bool) -> str:
    now = now_utc_ms()
    media_uuid = str(_uuid.uuid4())
    db.add(
        Media(
            uuid=media_uuid,
            original_filename=f"{username}.jpg",
            media_type="image",
            mime_type="image/jpeg",
            original_mime_type="image/jpeg",
            original_extension="jpg",
            file_size=1024,
            preserve_original=0,
            conversion_status="completed",
            uploaded_by=username,
            access_roles='["public"]' if public else '["self", "admin", "coach"]',
            is_encrypted=False,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        UserMediaLink(
            username=username,
            media_uuid=media_uuid,
            tag="user_avatar",
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    return media_uuid


@pytest.mark.asyncio
async def test_public_profile_by_id_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: anonymous caller gets an opted-in coach's public profile."""
    await _make_coach(
        db_session,
        "coach1",
        is_public_profile=True,
        bio="Loves hockey.",
        achievements="National champion.",
    )
    await db_session.commit()

    public_id = generate_public_id("coach1")
    resp = await client.get(f"/v1/public/profile/by_id/{public_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["publicId"] == public_id
    assert body["displayName"] == "Coach"
    assert body["bio"] == "Loves hockey."
    assert body["achievements"] == "National champion."
    assert "username" not in body


@pytest.mark.asyncio
async def test_public_profile_unknown_id_404(client: AsyncClient):
    """Issue 266: an unknown public_id is a 404."""
    resp = await client.get("/v1/public/profile/by_id/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_profile_opted_out_coach_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: a coach with is_public_profile=false is not resolvable."""
    await _make_coach(db_session, "private_coach", is_public_profile=False)
    await db_session.commit()
    resp = await client.get(
        f"/v1/public/profile/by_id/{generate_public_id('private_coach')}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_profile_non_coach_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: a non-coach is not public even if the flag is set."""
    await _make_coach(db_session, "flagged_member", is_public_profile=True, role="")
    await db_session.commit()
    resp = await client.get(
        f"/v1/public/profile/by_id/{generate_public_id('flagged_member')}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_profile_privacy_name(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: display_name honors use_name_publicly (placeholder when off)."""
    await _make_coach(
        db_session,
        "shy_coach",
        is_public_profile=True,
        use_name_publicly=False,
        first_name="Secret",
    )
    await db_session.commit()
    resp = await client.get(
        f"/v1/public/profile/by_id/{generate_public_id('shy_coach')}"
    )
    assert resp.status_code == 200
    assert resp.json()["displayName"] == "Name not provided"


@pytest.mark.asyncio
async def test_public_profile_avatar_public_only(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: avatar uuid surfaces only when the latest avatar is public."""
    await _make_coach(db_session, "pub_avatar", is_public_profile=True)
    await _make_coach(db_session, "priv_avatar", is_public_profile=True)
    pub_uuid = await _attach_avatar(db_session, "pub_avatar", public=True)
    await _attach_avatar(db_session, "priv_avatar", public=False)
    await db_session.commit()

    resp_pub = await client.get(
        f"/v1/public/profile/by_id/{generate_public_id('pub_avatar')}"
    )
    assert resp_pub.json()["avatar"]["uuid"] == pub_uuid
    resp_priv = await client.get(
        f"/v1/public/profile/by_id/{generate_public_id('priv_avatar')}"
    )
    assert resp_priv.json()["avatar"] is None


@pytest.mark.asyncio
async def test_public_staff_lists_opted_in_only(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: /public/staff lists only opted-in coaches."""
    await _make_coach(db_session, "staff_in", is_public_profile=True)
    await _make_coach(db_session, "staff_out", is_public_profile=False)
    await db_session.commit()

    resp = await client.get("/v1/public/staff")
    assert resp.status_code == 200
    ids = [p["publicId"] for p in resp.json()]
    assert generate_public_id("staff_in") in ids
    assert generate_public_id("staff_out") not in ids


@pytest.mark.asyncio
async def test_user_info_public_fields(client: AsyncClient, db_session: AsyncSession):
    """Issue 266: UserInfoResponse exposes publicId (HMAC) + stored flag."""
    viewer_token = await create_member_user(db_session, "viewer")
    await _make_coach(db_session, "opted_in", is_public_profile=True)
    await _make_coach(db_session, "opted_out", is_public_profile=False)
    await db_session.commit()

    headers = {"Authorization": f"Bearer {viewer_token}"}
    a = (await client.get("/v1/users/by_id/opted_in", headers=headers)).json()
    assert a["publicId"] == generate_public_id("opted_in")
    assert a["isPublicProfile"] is True
    b = (await client.get("/v1/users/by_id/opted_out", headers=headers)).json()
    assert b["isPublicProfile"] is False


@pytest.mark.requirement("users:R39")
@pytest.mark.asyncio
async def test_self_coach_can_set_is_public_profile(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: a coach can toggle their own is_public_profile."""
    await _make_coach(db_session, "selfcoach", is_public_profile=False)
    await db_session.commit()
    token = AuthService.create_access_token("selfcoach", is_super_admin=False)

    resp = await client.patch(
        "/v1/users/by_id/selfcoach",
        headers={"Authorization": f"Bearer {token}"},
        json={"isPublicProfile": True},
    )
    assert resp.status_code == 200
    assert resp.json()["isPublicProfile"] is True


@pytest.mark.requirement("users:R39")
@pytest.mark.asyncio
async def test_admin_cannot_override_is_public_profile(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: an admin PATCHing another user's flag is ignored."""
    admin_token = await create_admin_user(db_session)
    await _make_coach(db_session, "victim_coach", is_public_profile=False)
    await db_session.commit()

    resp = await client.patch(
        "/v1/users/by_id/victim_coach",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"isPublicProfile": True},
    )
    assert resp.status_code == 200
    # Admin override is silently ignored — still false.
    assert resp.json()["isPublicProfile"] is False


@pytest.mark.requirement("users:R39")
@pytest.mark.asyncio
async def test_non_coach_self_cannot_set_is_public_profile(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: a non-coach setting their own flag stays false."""
    token = await create_member_user(db_session, "plainmember")
    await db_session.commit()

    resp = await client.patch(
        "/v1/users/by_id/plainmember",
        headers={"Authorization": f"Bearer {token}"},
        json={"isPublicProfile": True},
    )
    assert resp.status_code == 200
    assert resp.json()["isPublicProfile"] is False


@pytest.mark.requirement("users:R39")
@pytest.mark.asyncio
async def test_turning_off_public_profile_forces_use_name_publicly_off(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 266: is_public_profile=false forces use_name_publicly=false."""
    await _make_coach(
        db_session,
        "togglecoach",
        is_public_profile=True,
        use_name_publicly=True,
    )
    await db_session.commit()
    token = AuthService.create_access_token("togglecoach", is_super_admin=False)

    resp = await client.patch(
        "/v1/users/by_id/togglecoach",
        headers={"Authorization": f"Bearer {token}"},
        json={"isPublicProfile": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["isPublicProfile"] is False
    assert body["useNamePublicly"] is False
