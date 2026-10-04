"""Which tokens the server accepts (#461).

A refresh token is not an access token, and no token issued before the
user's password last changed is accepted — on the access-token paths, the
media download's optional auth, or ``/auth/refresh``. Tokens minted before
this change carry neither ``type`` nor ``iat``; they keep working until they
expire, unless the user's password has changed since the change shipped.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.user import User
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_media_row, create_member_user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mint(username: str, **claims: object) -> str:
    """A token signed with the server key, carrying exactly ``claims``."""
    body: dict[str, object] = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        "is_super_admin": False,
    }
    body.update(claims)
    return jwt.encode(body, settings.secret_key, algorithm=settings.algorithm)


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _set_password_changed_at(
    db_session: AsyncSession, username: str, when_ms: int
) -> None:
    user = (
        await db_session.execute(select(User).where(User.username == username))
    ).scalar_one()
    user.password_changed_at = when_ms
    await db_session.commit()


async def _password_changed_at(db_session: AsyncSession, username: str) -> int | None:
    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == username))
    ).scalar_one()
    return user.password_changed_at


# --- refresh token on an access path -------------------------------------------


@pytest.mark.requirement("auth:R9")
@pytest.mark.asyncio
async def test_should_refuse_refresh_token_when_used_as_access_token(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "pat")
    await db_session.commit()
    tokens = await _login(client, "pat", "memberpass123")

    response = await client.get("/v1/auth/me", headers=_auth(tokens["refreshToken"]))
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "INVALID_TOKEN"

    accepted = await client.get("/v1/auth/me", headers=_auth(tokens["accessToken"]))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["username"] == "pat"


@pytest.mark.requirement("auth:R10")
@pytest.mark.asyncio
async def test_should_accept_legacy_token_without_type_or_iat(
    client: AsyncClient, db_session: AsyncSession
):
    """A token minted before #461 still works while the password is unchanged."""
    _ = await create_member_user(db_session, "pat")
    await db_session.commit()

    response = await client.get("/v1/auth/me", headers=_auth(_mint("pat")))
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "pat"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R32")
async def test_should_treat_refresh_token_as_anonymous_on_media_download(
    client: AsyncClient, db_session: AsyncSession
):
    """The download's optional auth does not take a refresh token either."""
    _ = await create_member_user(db_session, "pat")
    media_uuid = await create_media_row(db_session, uploaded_by="pat", public=False)
    await db_session.commit()
    tokens = await _login(client, "pat", "memberpass123")

    response = await client.get(
        f"/v1/media/by_id/{media_uuid}/download",
        headers=_auth(tokens["refreshToken"]),
    )
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


# --- password change invalidates earlier tokens ------------------------------


@pytest.mark.requirement("auth:R19")
@pytest.mark.asyncio
async def test_should_refuse_old_tokens_when_user_changes_password(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "pat")
    await db_session.commit()
    old = await _login(client, "pat", "memberpass123")

    changed = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": "memberpass123", "newPassword": "brandnew456"},
        headers=_auth(old["accessToken"]),
    )
    assert changed.status_code == 204, changed.text
    assert await _password_changed_at(db_session, "pat") is not None

    stale_access = await client.get("/v1/auth/me", headers=_auth(old["accessToken"]))
    assert stale_access.status_code == 401, stale_access.text
    assert stale_access.json()["detail"]["code"] == "INVALID_TOKEN"

    stale_refresh = await client.post(
        "/v1/auth/refresh", json={"refreshToken": old["refreshToken"]}
    )
    assert stale_refresh.status_code == 401, stale_refresh.text
    assert stale_refresh.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"

    fresh = await _login(client, "pat", "brandnew456")
    me = await client.get("/v1/auth/me", headers=_auth(fresh["accessToken"]))
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "pat"
    refreshed = await client.post(
        "/v1/auth/refresh", json={"refreshToken": fresh["refreshToken"]}
    )
    assert refreshed.status_code == 200, refreshed.text


@pytest.mark.requirement("auth:R19")
@pytest.mark.asyncio
async def test_should_refuse_old_token_when_admin_resets_password(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "pat")
    await db_session.commit()
    old = await _login(client, "pat", "memberpass123")

    reset = await client.post("/v1/admin/reset-password/pat", headers=_auth(admin))
    assert reset.status_code == 200, reset.text
    new_password = reset.json()["newPassword"]

    stale = await client.get("/v1/auth/me", headers=_auth(old["accessToken"]))
    assert stale.status_code == 401, stale.text
    assert stale.json()["detail"]["code"] == "INVALID_TOKEN"

    fresh = await _login(client, "pat", new_password)
    me = await client.get("/v1/auth/me", headers=_auth(fresh["accessToken"]))
    assert me.status_code == 200, me.text


@pytest.mark.requirement("auth:R20")
@pytest.mark.asyncio
async def test_should_refuse_token_without_iat_when_password_has_changed(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "pat")
    await _set_password_changed_at(db_session, "pat", now_utc_ms() - 60_000)

    response = await client.get("/v1/auth/me", headers=_auth(_mint("pat")))
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "INVALID_TOKEN"


@pytest.mark.requirement("auth:R20")
@pytest.mark.asyncio
async def test_should_refuse_token_issued_earlier_in_the_same_second_as_change(
    client: AsyncClient, db_session: AsyncSession
):
    """Whole seconds are not enough: a token from the same second but before
    the change is refused."""
    _ = await create_member_user(db_session, "pat")
    changed_ms = (now_utc_ms() // 1000 - 5) * 1000 + 600
    await _set_password_changed_at(db_session, "pat", changed_ms)

    before = _mint("pat", iat=(changed_ms - 300) / 1000)
    response = await client.get("/v1/auth/me", headers=_auth(before))
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "INVALID_TOKEN"


@pytest.mark.requirement("auth:R20")
@pytest.mark.asyncio
async def test_should_accept_token_issued_later_in_the_same_second_as_change(
    client: AsyncClient, db_session: AsyncSession
):
    """A token minted just after the change, within the same second, works."""
    _ = await create_member_user(db_session, "pat")
    changed_ms = (now_utc_ms() // 1000 - 5) * 1000 + 300
    await _set_password_changed_at(db_session, "pat", changed_ms)

    after = _mint("pat", iat=(changed_ms + 300) / 1000)
    response = await client.get("/v1/auth/me", headers=_auth(after))
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "pat"


@pytest.mark.requirement("auth:R20")
@pytest.mark.asyncio
async def test_should_accept_token_issued_at_the_change_instant(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "pat")
    changed_ms = now_utc_ms() - 5_000
    await _set_password_changed_at(db_session, "pat", changed_ms)

    same = _mint("pat", iat=changed_ms / 1000)
    response = await client.get("/v1/auth/me", headers=_auth(same))
    assert response.status_code == 200, response.text
