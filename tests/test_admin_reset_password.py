import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.user import User, UserStatus
from club_server.mailer import EmailMessage, SendResult
from club_server.mailer.sender import ConsoleEmailSender
from club_server.routers import admin as admin_module
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_regular_admin_user


@pytest.fixture(autouse=True)
def _clear_outbox():
    ConsoleEmailSender.clear()
    yield
    ConsoleEmailSender.clear()


async def create_active_user(
    db_session: AsyncSession,
    username: str = "testuser",
    *,
    email: str | None = None,
) -> None:
    """Create an active regular user in the database."""
    user = User(
        username=username,
        email=email,
        password=AuthService.hash_password("oldpass123"),
        first_name="Test",
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()


async def _reset_audit_details(db_session: AsyncSession, target: str) -> dict:
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "admin_password_reset",
                    AuditLog.target_username == target,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    return json.loads(rows[0].details or "{}")


@pytest.mark.requirement("platform:R8")
@pytest.mark.asyncio
async def test_admin_reset_password_success(
    client: AsyncClient, db_session: AsyncSession
):
    """Test super admin can reset a user's password and receives the new password."""
    super_admin_token = await create_admin_user(db_session)
    await create_active_user(db_session, "testuser")

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "newPassword" in data
    assert len(data["newPassword"]) >= 8


@pytest.mark.requirement("platform:R8")
@pytest.mark.asyncio
async def test_admin_reset_password_new_password_works_for_login(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that the returned password actually works for login."""
    super_admin_token = await create_admin_user(db_session)
    await create_active_user(db_session, "testuser")

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200
    new_password = response.json()["newPassword"]

    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": new_password},
    )
    assert login_response.status_code == 200


@pytest.mark.requirement("platform:R8")
@pytest.mark.asyncio
async def test_admin_reset_password_old_password_no_longer_works(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that the old password no longer works after reset."""
    super_admin_token = await create_admin_user(db_session)
    await create_active_user(db_session, "testuser")

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200

    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": "oldpass123"},
    )
    assert login_response.status_code == 401


@pytest.mark.requirement("platform:R10")
@pytest.mark.asyncio
async def test_admin_reset_password_rejects_super_admin_target(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that resetting a super admin's password is rejected."""
    super_admin_token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/admin/reset-password/admin",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CANNOT_RESET_SUPER_ADMIN"


@pytest.mark.requirement("platform:R8")
@pytest.mark.asyncio
async def test_regular_admin_can_reset_password(
    client: AsyncClient, db_session: AsyncSession
):
    """A regular admin (not super admin) can now reset a user's password (#269)."""
    regular_admin_token = await create_regular_admin_user(db_session)
    await create_active_user(db_session, "testuser")

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 200
    new_password = response.json()["newPassword"]
    login = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": new_password},
    )
    assert login.status_code == 200


@pytest.mark.requirement("platform:R9")
@pytest.mark.asyncio
async def test_admin_reset_emails_password_to_user_with_email(
    client: AsyncClient, db_session: AsyncSession
):
    """When the target has an email, the new password is emailed (T2) and the
    audit row records emailSent=True (#269)."""
    super_admin_token = await create_admin_user(db_session)
    await create_active_user(db_session, "testuser", email="testuser@example.com")

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200
    returned_password = response.json()["newPassword"]

    assert len(ConsoleEmailSender.outbox) == 1
    sent = ConsoleEmailSender.outbox[0]
    assert sent.to == "testuser@example.com"
    assert returned_password in (sent.text or "")
    assert "administrator" in sent.subject.lower()

    details = await _reset_audit_details(db_session, "testuser")
    assert details["emailSent"] is True


@pytest.mark.requirement("platform:R9")
@pytest.mark.asyncio
async def test_admin_reset_user_without_email_still_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    """No email on file: still returns the plaintext to the admin, sends no
    email, and records emailSent=False (#269)."""
    super_admin_token = await create_admin_user(db_session)
    await create_active_user(db_session, "testuser")  # no email

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200
    assert "newPassword" in response.json()
    assert ConsoleEmailSender.outbox == []

    details = await _reset_audit_details(db_session, "testuser")
    assert details["emailSent"] is False


@pytest.mark.requirement("platform:R9")
@pytest.mark.asyncio
async def test_admin_reset_email_failure_is_best_effort(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A delivery failure must not fail the request — the admin still gets the
    plaintext and the password is still changed; audit records emailSent=False."""
    super_admin_token = await create_admin_user(db_session)
    await create_active_user(db_session, "testuser", email="testuser@example.com")

    class _FailingSender:
        name = "failing"

        async def send(self, _message: EmailMessage) -> SendResult:
            return SendResult(success=False, provider="failing", error="boom")

    monkeypatch.setattr(
        admin_module, "get_transactional_sender", lambda: _FailingSender()
    )

    response = await client.post(
        "/v1/admin/reset-password/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200
    new_password = response.json()["newPassword"]
    login = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": new_password},
    )
    assert login.status_code == 200

    details = await _reset_audit_details(db_session, "testuser")
    assert details["emailSent"] is False


@pytest.mark.requirement("platform:R11")
@pytest.mark.asyncio
async def test_admin_reset_password_user_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that resetting password for nonexistent user returns 404."""
    super_admin_token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/admin/reset-password/nonexistent",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("platform:R11")
@pytest.mark.asyncio
async def test_admin_reset_password_without_auth(client: AsyncClient):
    """Test that unauthenticated requests are rejected."""
    response = await client.post("/v1/admin/reset-password/testuser")
    assert response.status_code == 401
