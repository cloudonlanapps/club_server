"""Tests for auth rules that had no proving test (#496): login and tokens.

Each test names the rule it proves in ``docs/auth_requirements.md``.
Passwords, email and audit are in ``test_auth_password_requirement_gaps.py``.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.user import UserStatus
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_member_user, create_user_with_status

STATUS_PASSWORD = "statuspass123"
MEMBER_PASSWORD = "memberpass123"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.requirement("auth:R5")
@pytest.mark.asyncio
async def test_should_refuse_login_when_user_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )
    await db_session.commit()

    response = await client.post(
        "/v1/auth/login", json={"username": "gone", "password": STATUS_PASSWORD}
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.requirement("auth:R6")
@pytest.mark.asyncio
async def test_should_refuse_login_when_user_has_left(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_user_with_status(db_session, "lea", UserStatus.left)
    await db_session.commit()

    response = await client.post(
        "/v1/auth/login", json={"username": "lea", "password": STATUS_PASSWORD}
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "ACCOUNT_LEFT"
    admin = await create_admin_user(db_session)
    await db_session.commit()
    profile = await client.get("/v1/users/by_id/lea/private", headers=auth(admin))
    assert profile.status_code == 200, profile.text
    assert profile.json()["lastLoginAtUtc"] is None


@pytest.mark.requirement("auth:R6")
@pytest.mark.asyncio
async def test_should_allow_login_when_left_user_is_reactivated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(db_session, "lea", UserStatus.left)
    await db_session.commit()
    refused = await client.post(
        "/v1/auth/login", json={"username": "lea", "password": STATUS_PASSWORD}
    )
    assert refused.status_code == 401, refused.text

    back = await client.post("/v1/users/by_id/lea/reactivate", headers=auth(admin))
    assert back.status_code == 200, back.text

    tokens = await _login(client, "lea", STATUS_PASSWORD)
    me = await client.get("/v1/auth/me", headers=auth(tokens["accessToken"]))
    assert me.status_code == 200, me.text
    assert me.json()["status"] == "active"


@pytest.mark.requirement("auth:R7")
@pytest.mark.asyncio
async def test_should_record_last_login_when_user_logs_in(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    before = await client.get("/v1/users/by_id/amy/private", headers=auth(admin))
    assert before.json()["lastLoginAtUtc"] is None
    started = now_utc_ms()

    _ = await _login(client, "amy", MEMBER_PASSWORD)

    after = await client.get("/v1/users/by_id/amy/private", headers=auth(admin))
    assert after.status_code == 200, after.text
    assert started <= after.json()["lastLoginAtUtc"] <= now_utc_ms()


@pytest.mark.requirement("auth:R13")
@pytest.mark.asyncio
async def test_should_refuse_token_when_user_has_left(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_user_with_status(db_session, "lea", UserStatus.left)

    response = await client.get("/v1/auth/me", headers=auth(token))

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "ACCOUNT_LEFT"


@pytest.mark.requirement("auth:R14")
@pytest.mark.asyncio
async def test_should_refuse_token_when_user_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )

    response = await client.get("/v1/auth/me", headers=auth(token))

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("auth:R16a")
@pytest.mark.asyncio
async def test_should_refuse_refresh_when_given_an_access_token(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    tokens = await _login(client, "amy", MEMBER_PASSWORD)

    response = await client.post(
        "/v1/auth/refresh", json={"refreshToken": tokens["accessToken"]}
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.requirement("auth:R17")
@pytest.mark.asyncio
async def test_should_refuse_refresh_when_user_is_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()
    tokens = await _login(client, "amy", MEMBER_PASSWORD)
    blocked = await client.post("/v1/users/by_id/amy/block", headers=auth(admin))
    assert blocked.status_code == 200, blocked.text

    response = await client.post(
        "/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "ACCOUNT_BLOCKED"


@pytest.mark.requirement("auth:R17a")
@pytest.mark.asyncio
async def test_should_refuse_refresh_when_user_has_left(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()
    tokens = await _login(client, "amy", MEMBER_PASSWORD)
    left = await client.post("/v1/users/by_id/amy/mark-left", headers=auth(admin))
    assert left.status_code == 200, left.text

    response = await client.post(
        "/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "ACCOUNT_LEFT"
    feed = await client.get(
        "/v1/audit_log", params={"action": "token_refreshed"}, headers=auth(admin)
    )
    assert feed.status_code == 200, feed.text
    assert feed.json()["rows"] == []


@pytest.mark.requirement("auth:R18")
@pytest.mark.asyncio
async def test_should_state_token_lifetimes_when_tokens_are_issued(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    access_life = settings.access_token_expire_minutes * 60_000
    refresh_life = int(timedelta(days=30).total_seconds() * 1000)
    started = now_utc_ms()

    tokens = await _login(client, "amy", MEMBER_PASSWORD)

    finished = now_utc_ms()
    assert started + access_life - 1000 <= tokens["expiresAtUtc"]
    assert tokens["expiresAtUtc"] <= finished + access_life
    access = jwt.get_unverified_claims(tokens["accessToken"])
    assert abs(access["exp"] * 1000 - tokens["expiresAtUtc"]) <= 1000
    refresh = jwt.get_unverified_claims(tokens["refreshToken"])
    assert started + refresh_life - 1000 <= refresh["exp"] * 1000
    assert refresh["exp"] * 1000 <= finished + refresh_life


@pytest.mark.requirement("auth:R22")
@pytest.mark.asyncio
async def test_should_refuse_token_when_user_logs_out(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_member_user(db_session, "amy")
    tokens = await _login(client, "amy", MEMBER_PASSWORD)

    logout = await client.post("/v1/auth/logout", headers=auth(tokens["accessToken"]))
    assert logout.status_code == 204, logout.text

    me = await client.get("/v1/auth/me", headers=auth(tokens["accessToken"]))
    assert me.status_code == 401, me.text
    assert me.json()["detail"]["code"] == "INVALID_TOKEN"
