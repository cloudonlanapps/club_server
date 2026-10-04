"""Tests for auth rules that had no proving test (#496): passwords, email, audit.

Each test names the rule it proves in ``docs/auth_requirements.md``.
Login and tokens are in ``test_auth_requirement_gaps.py``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.mailer.sender import ConsoleEmailSender

from .helpers import create_admin_user, create_member_user, create_registered_user

MEMBER_PASSWORD = "memberpass123"
CLIENT_IP = "9.9.9.9"


@pytest.fixture(autouse=True)
def _clear_outbox():
    ConsoleEmailSender.clear()
    yield
    ConsoleEmailSender.clear()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _member_with_email(
    client: AsyncClient, db_session: AsyncSession, admin: str, email: str
) -> None:
    _ = await create_member_user(db_session, "pat")
    given = await client.patch(
        "/v1/users/by_id/pat", json={"email": email}, headers=auth(admin)
    )
    assert given.status_code == 200, given.text
    await db_session.commit()


async def _reset_audit(client: AsyncClient, admin: str) -> dict:
    response = await client.get(
        "/v1/audit_log",
        params={"action": "password_reset_requested"},
        headers=auth(admin),
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert len(rows) == 1, rows
    return rows[0]["details"]


@pytest.mark.requirement("auth:R19a")
@pytest.mark.asyncio
async def test_should_refuse_earlier_tokens_when_password_is_reset_by_email(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _member_with_email(client, db_session, admin, "pat@example.com")
    old = await _login(client, "pat", MEMBER_PASSWORD)

    reset = await client.post(
        "/v1/auth/reset-password", json={"email": "pat@example.com"}
    )
    assert reset.status_code == 204, reset.text
    assert len(ConsoleEmailSender.outbox) == 1

    stale = await client.get("/v1/auth/me", headers=auth(old["accessToken"]))
    assert stale.status_code == 401, stale.text
    assert stale.json()["detail"]["code"] == "INVALID_TOKEN"
    stale_refresh = await client.post(
        "/v1/auth/refresh", json={"refreshToken": old["refreshToken"]}
    )
    assert stale_refresh.status_code == 401, stale_refresh.text
    assert stale_refresh.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.requirement("auth:R26")
@pytest.mark.asyncio
async def test_should_change_password_when_account_is_not_active(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_registered_user(db_session, "rosa")
    await db_session.commit()

    response = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": "regpass123", "newPassword": "brandnew123"},
        headers=auth(token),
    )

    assert response.status_code == 204, response.text
    _ = await _login(client, "rosa", "brandnew123")


@pytest.mark.requirement("auth:R27")
@pytest.mark.asyncio
async def test_should_accept_any_non_empty_password_when_changing_it(
    client: AsyncClient, db_session: AsyncSession
):
    amy = await create_member_user(db_session, "amy")

    response = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": MEMBER_PASSWORD, "newPassword": "x"},
        headers=auth(amy),
    )

    assert response.status_code == 204, response.text
    _ = await _login(client, "amy", "x")


@pytest.mark.requirement("auth:R32")
@pytest.mark.asyncio
async def test_should_reset_password_when_address_differs_only_in_case(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _member_with_email(client, db_session, admin, "pat@example.com")

    reset = await client.post(
        "/v1/auth/reset-password", json={"email": "Pat@Example.com"}
    )
    assert reset.status_code == 204, reset.text

    assert (await _reset_audit(client, admin))["accountExists"] is True
    assert [m.to for m in ConsoleEmailSender.outbox] == ["pat@example.com"]


@pytest.mark.requirement("auth:R32")
@pytest.mark.asyncio
async def test_should_reset_password_when_stored_address_has_capitals(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _member_with_email(client, db_session, admin, "Pat@example.com")

    reset = await client.post(
        "/v1/auth/reset-password", json={"email": "pat@example.com"}
    )
    assert reset.status_code == 204, reset.text

    assert (await _reset_audit(client, admin))["accountExists"] is True
    assert [m.to for m in ConsoleEmailSender.outbox] == ["Pat@example.com"]
    refused = await client.post(
        "/v1/auth/login", json={"username": "pat", "password": MEMBER_PASSWORD}
    )
    assert refused.status_code == 401, refused.text


@pytest.mark.requirement("auth:R33")
@pytest.mark.asyncio
async def test_should_reject_reset_when_value_is_not_an_email_address(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.post(
        "/v1/auth/reset-password", json={"email": "not-an-address"}
    )

    assert response.status_code == 422, response.text
    assert ConsoleEmailSender.outbox == []


@pytest.mark.requirement("auth:R34")
@pytest.mark.asyncio
async def test_should_send_no_email_when_user_logs_in_or_changes_password(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _member_with_email(client, db_session, admin, "pat@example.com")
    tokens = await _login(client, "pat", MEMBER_PASSWORD)

    changed = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": MEMBER_PASSWORD, "newPassword": "brandnew123"},
        headers=auth(tokens["accessToken"]),
    )
    assert changed.status_code == 204, changed.text

    _ = await _login(client, "pat", "brandnew123")
    assert ConsoleEmailSender.outbox == []


@pytest.mark.requirement("auth:R35")
@pytest.mark.asyncio
async def test_should_audit_each_sign_in_flow_with_user_and_address(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    from_ip = {"X-Forwarded-For": CLIENT_IP}
    registered = await client.post(
        "/v1/auth/register",
        json={
            "username": "newbie",
            "password": "newbiepass123",
            "firstName": "New",
            "gender": "female",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
        headers=from_ip,
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(
        "/v1/auth/login",
        json={"username": "amy", "password": MEMBER_PASSWORD},
        headers=from_ip,
    )
    assert login.status_code == 200, login.text
    access = {**auth(login.json()["accessToken"]), **from_ip}
    refreshed = await client.post(
        "/v1/auth/refresh",
        json={"refreshToken": login.json()["refreshToken"]},
        headers=from_ip,
    )
    assert refreshed.status_code == 200, refreshed.text
    changed = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": MEMBER_PASSWORD, "newPassword": "brandnew123"},
        headers=access,
    )
    assert changed.status_code == 204, changed.text
    fresh = await _login(client, "amy", "brandnew123")
    logout = await client.post(
        "/v1/auth/logout", headers={**auth(fresh["accessToken"]), **from_ip}
    )
    assert logout.status_code == 204, logout.text

    for action, actor in (
        ("register", "newbie"),
        ("token_refreshed", "amy"),
        ("password_changed", "amy"),
        ("logout", "amy"),
    ):
        response = await client.get(
            "/v1/audit_log", params={"action": action}, headers=auth(admin)
        )
        rows = response.json()["rows"]
        assert [r["actor"]["username"] for r in rows] == [actor], action
        assert rows[0]["details"]["ip_address"] == CLIENT_IP, action
    logins = await client.get(
        "/v1/audit_log", params={"action": "login", "actor": "amy"}, headers=auth(admin)
    )
    assert logins.json()["total"] == 2
    assert logins.json()["rows"][-1]["details"]["ip_address"] == CLIENT_IP
