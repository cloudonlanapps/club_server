"""Tests for #268 — forgot-password generate-and-email flow.

The endpoint `POST /v1/auth/reset-password` previously minted a JWT and threw
it away (a no-op). It now generates a new password, emails it, and only then
persists it. The HTTP response is always a uniform 204 (anti-enumeration); the
real outcome lands only in the audit log.

Default email provider in tests is the console sender, so sends succeed and the
generated password is observable via ConsoleEmailSender.outbox.
"""

import json
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.user import User, UserStatus
from club_server.mailer import EmailMessage, SendResult
from club_server.mailer.sender import ConsoleEmailSender
from club_server.services import auth as auth_module
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms


@pytest.fixture(autouse=True)
def _clear_outbox():
    ConsoleEmailSender.clear()
    yield
    ConsoleEmailSender.clear()


async def _create_user_with_email(
    db_session: AsyncSession, username: str, email: str, password: str
) -> None:
    db_session.add(
        User(
            username=username,
            email=email,
            password=AuthService.hash_password(password),
            first_name="Pat",
            status=UserStatus.active.value,
            is_super_admin=0,
            roles=json.dumps({"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await db_session.flush()


def _password_from_outbox() -> str:
    assert len(ConsoleEmailSender.outbox) == 1
    text = ConsoleEmailSender.outbox[0].text or ""
    match = re.search(r"temporary password is: (\S+)", text)
    assert match, f"no password found in email text: {text!r}"
    return match.group(1)


async def _reset_action_details(db_session: AsyncSession) -> dict:
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "password_reset_requested")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    return json.loads(rows[0].details or "{}")


@pytest.mark.requirement("auth:R28")
@pytest.mark.requirement("auth:R31")
@pytest.mark.asyncio
async def test_unknown_email_is_noop(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/v1/auth/reset-password", json={"email": "nobody@example.com"}
    )
    assert resp.status_code == 204
    assert resp.content == b""
    assert ConsoleEmailSender.outbox == []

    details = await _reset_action_details(db_session)
    assert details["accountExists"] is False
    assert details["emailSent"] is False


@pytest.mark.requirement("auth:R29")
@pytest.mark.requirement("auth:R31")
@pytest.mark.asyncio
async def test_known_email_resets_and_emails(
    client: AsyncClient, db_session: AsyncSession
):
    await _create_user_with_email(db_session, "pat", "pat@example.com", "oldpass123")

    resp = await client.post(
        "/v1/auth/reset-password", json={"email": "pat@example.com"}
    )
    assert resp.status_code == 204
    assert resp.content == b""

    # Email went to the right address with the generated password.
    assert len(ConsoleEmailSender.outbox) == 1
    assert ConsoleEmailSender.outbox[0].to == "pat@example.com"
    new_password = _password_from_outbox()

    # Old password no longer works; the emailed one does.
    old_login = await client.post(
        "/v1/auth/login", json={"username": "pat", "password": "oldpass123"}
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/v1/auth/login", json={"username": "pat", "password": new_password}
    )
    assert new_login.status_code == 200

    details = await _reset_action_details(db_session)
    assert details["accountExists"] is True
    assert details["emailSent"] is True


@pytest.mark.requirement("auth:R28")
@pytest.mark.asyncio
async def test_response_uniform_for_known_and_unknown(
    client: AsyncClient, db_session: AsyncSession
):
    await _create_user_with_email(db_session, "pat", "pat@example.com", "oldpass123")
    known = await client.post(
        "/v1/auth/reset-password", json={"email": "pat@example.com"}
    )
    unknown = await client.post(
        "/v1/auth/reset-password", json={"email": "ghost@example.com"}
    )
    assert known.status_code == unknown.status_code == 204
    assert known.content == unknown.content == b""


@pytest.mark.requirement("auth:R30")
@pytest.mark.requirement("auth:R31")
@pytest.mark.asyncio
async def test_send_failure_keeps_old_password(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """If the email cannot be delivered we must NOT change the password —
    otherwise the user is locked out with a password they never received."""
    await _create_user_with_email(db_session, "pat", "pat@example.com", "oldpass123")

    class _FailingSender:
        name = "failing"

        async def send(self, _message: EmailMessage) -> SendResult:
            return SendResult(success=False, provider="failing", error="boom")

    monkeypatch.setattr(
        auth_module, "get_transactional_sender", lambda: _FailingSender()
    )

    resp = await client.post(
        "/v1/auth/reset-password", json={"email": "pat@example.com"}
    )
    assert resp.status_code == 204

    # Old password still works — password was not changed.
    old_login = await client.post(
        "/v1/auth/login", json={"username": "pat", "password": "oldpass123"}
    )
    assert old_login.status_code == 200

    details = await _reset_action_details(db_session)
    assert details["accountExists"] is True
    assert details["emailSent"] is False
