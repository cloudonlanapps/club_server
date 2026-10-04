"""Logout ends the session it is called with (#510).

A session begins at login. Its access and refresh tokens, and every pair
``/auth/refresh`` issues from them, belong to it; logging out with any of
its access tokens refuses them all. Other sessions of the same user are
untouched. Any logged-in user may log out and change their own password,
whatever their status. Rules auth:R21-R22b and R26 in
``docs/auth_requirements.md``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.user import UserStatus

from .helpers import create_admin_user, create_member_user, create_user_with_status

MEMBER_PASSWORD = "memberpass123"
STATUS_PASSWORD = "statuspass123"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _refresh(client: AsyncClient, refresh_token: str) -> dict:
    response = await client.post(
        "/v1/auth/refresh", json={"refreshToken": refresh_token}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _logout(client: AsyncClient, access_token: str) -> None:
    response = await client.post("/v1/auth/logout", headers=auth(access_token))
    assert response.status_code == 204, response.text
    assert response.content == b""


async def _assert_access_refused(client: AsyncClient, access_token: str) -> None:
    me = await client.get("/v1/auth/me", headers=auth(access_token))
    assert me.status_code == 401, me.text
    assert me.json()["detail"]["code"] == "INVALID_TOKEN"


async def _assert_refresh_refused(client: AsyncClient, refresh_token: str) -> None:
    response = await client.post(
        "/v1/auth/refresh", json={"refreshToken": refresh_token}
    )
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.requirement("auth:R22")
@pytest.mark.asyncio
async def test_should_refuse_access_token_when_its_session_logged_out(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    tokens = await _login(client, "amy", MEMBER_PASSWORD)

    await _logout(client, tokens["accessToken"])

    await _assert_access_refused(client, tokens["accessToken"])


@pytest.mark.requirement("auth:R22")
@pytest.mark.asyncio
async def test_should_refuse_refresh_token_when_its_session_logged_out(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    tokens = await _login(client, "amy", MEMBER_PASSWORD)

    await _logout(client, tokens["accessToken"])

    await _assert_refresh_refused(client, tokens["refreshToken"])


@pytest.mark.requirement("auth:R22a")
@pytest.mark.asyncio
async def test_should_refuse_refreshed_tokens_when_session_logs_out_with_first_token(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    first = await _login(client, "amy", MEMBER_PASSWORD)
    second = await _refresh(client, first["refreshToken"])
    me = await client.get("/v1/auth/me", headers=auth(second["accessToken"]))
    assert me.status_code == 200, me.text

    await _logout(client, first["accessToken"])

    await _assert_access_refused(client, second["accessToken"])
    await _assert_refresh_refused(client, second["refreshToken"])


@pytest.mark.requirement("auth:R22a")
@pytest.mark.asyncio
async def test_should_refuse_original_tokens_when_session_logs_out_with_refreshed_token(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    first = await _login(client, "amy", MEMBER_PASSWORD)
    second = await _refresh(client, first["refreshToken"])

    await _logout(client, second["accessToken"])

    await _assert_access_refused(client, first["accessToken"])
    await _assert_refresh_refused(client, first["refreshToken"])
    await _assert_refresh_refused(client, second["refreshToken"])


@pytest.mark.requirement("auth:R22b")
@pytest.mark.asyncio
async def test_should_keep_other_session_working_when_one_session_logs_out(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    phone = await _login(client, "amy", MEMBER_PASSWORD)
    laptop = await _login(client, "amy", MEMBER_PASSWORD)

    await _logout(client, phone["accessToken"])

    me = await client.get("/v1/auth/me", headers=auth(laptop["accessToken"]))
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "amy"
    renewed = await _refresh(client, laptop["refreshToken"])
    again = await client.get("/v1/auth/me", headers=auth(renewed["accessToken"]))
    assert again.status_code == 200, again.text
    await _assert_access_refused(client, phone["accessToken"])


@pytest.mark.requirement("auth:R22b")
@pytest.mark.asyncio
async def test_should_start_working_session_when_user_logs_in_after_logout(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    old = await _login(client, "amy", MEMBER_PASSWORD)
    await _logout(client, old["accessToken"])

    new = await _login(client, "amy", MEMBER_PASSWORD)

    me = await client.get("/v1/auth/me", headers=auth(new["accessToken"]))
    assert me.status_code == 200, me.text
    renewed = await _refresh(client, new["refreshToken"])
    again = await client.get("/v1/auth/me", headers=auth(renewed["accessToken"]))
    assert again.status_code == 200, again.text
    await _assert_access_refused(client, old["accessToken"])


@pytest.mark.requirement("auth:R22b")
@pytest.mark.asyncio
async def test_should_keep_other_users_session_when_admin_logs_out(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()
    admin = await _login(client, "admin", "adminpass123")
    amy = await _login(client, "amy", MEMBER_PASSWORD)

    await _logout(client, admin["accessToken"])

    me = await client.get("/v1/auth/me", headers=auth(amy["accessToken"]))
    assert me.status_code == 200, me.text
    await _assert_access_refused(client, admin["accessToken"])


@pytest.mark.requirement("auth:R21")
@pytest.mark.asyncio
@pytest.mark.parametrize("user_status", [UserStatus.registered, UserStatus.pending])
async def test_should_log_out_when_account_is_not_yet_active(
    client: AsyncClient, db_session: AsyncSession, user_status: UserStatus
):
    _ = await create_user_with_status(db_session, "rosa", user_status)
    await db_session.commit()
    tokens = await _login(client, "rosa", STATUS_PASSWORD)

    await _logout(client, tokens["accessToken"])

    await _assert_access_refused(client, tokens["accessToken"])
    await _assert_refresh_refused(client, tokens["refreshToken"])


@pytest.mark.requirement("auth:R21")
@pytest.mark.asyncio
async def test_should_refuse_logout_when_caller_is_anonymous(client: AsyncClient):
    response = await client.post("/v1/auth/logout")

    assert response.status_code == 401, response.text


@pytest.mark.requirement("auth:R21")
@pytest.mark.asyncio
async def test_should_refuse_second_logout_when_session_already_ended(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    tokens = await _login(client, "amy", MEMBER_PASSWORD)
    await _logout(client, tokens["accessToken"])

    again = await client.post("/v1/auth/logout", headers=auth(tokens["accessToken"]))

    assert again.status_code == 401, again.text
    assert again.json()["detail"]["code"] == "INVALID_TOKEN"


@pytest.mark.requirement("auth:R26")
@pytest.mark.asyncio
@pytest.mark.parametrize("user_status", [UserStatus.registered, UserStatus.pending])
async def test_should_change_password_when_account_is_not_yet_active(
    client: AsyncClient, db_session: AsyncSession, user_status: UserStatus
):
    _ = await create_user_with_status(db_session, "rosa", user_status)
    await db_session.commit()
    tokens = await _login(client, "rosa", STATUS_PASSWORD)

    response = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": STATUS_PASSWORD, "newPassword": "brandnew123"},
        headers=auth(tokens["accessToken"]),
    )

    assert response.status_code == 204, response.text
    _ = await _login(client, "rosa", "brandnew123")
    refused = await client.post(
        "/v1/auth/login", json={"username": "rosa", "password": STATUS_PASSWORD}
    )
    assert refused.status_code == 401, refused.text


@pytest.mark.requirement("auth:R22c")
@pytest.mark.asyncio
async def test_should_keep_token_without_session_working_when_it_logs_out(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()
    legacy = jwt.encode(
        {
            "sub": "amy",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "is_super_admin": False,
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )

    await _logout(client, legacy)

    me = await client.get("/v1/auth/me", headers=auth(legacy))
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "amy"


@pytest.mark.requirement("auth:R22")
@pytest.mark.asyncio
@pytest.mark.parametrize("body_key", ["refresh_token", "refreshToken"])
async def test_should_end_session_when_logout_body_carries_refresh_token(
    client: AsyncClient, db_session: AsyncSession, body_key: str
):
    _ = await create_member_user(db_session, "amy")
    tokens = await _login(client, "amy", MEMBER_PASSWORD)

    response = await client.post(
        "/v1/auth/logout",
        json={body_key: tokens["refreshToken"]},
        headers=auth(tokens["accessToken"]),
    )

    assert response.status_code == 204, response.text
    assert response.content == b""
    await _assert_access_refused(client, tokens["accessToken"])
    await _assert_refresh_refused(client, tokens["refreshToken"])
