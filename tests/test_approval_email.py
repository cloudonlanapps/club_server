"""Tests for #270 — email the user when an admin approves their account."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.user import User, UserStatus
from club_server.mailer.sender import ConsoleEmailSender
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms

from .helpers import create_admin_user


@pytest.fixture(autouse=True)
def _clear_outbox():
    ConsoleEmailSender.clear()
    yield
    ConsoleEmailSender.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_pending_user(
    db_session: AsyncSession, username: str, *, email: str | None
) -> None:
    db_session.add(
        User(
            username=username,
            email=email,
            password=AuthService.hash_password("pw12345678"),
            first_name="Pat",
            status=UserStatus.pending.value,
            is_super_admin=0,
            roles=json.dumps({"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await db_session.flush()


async def _approve_audit_details(db_session: AsyncSession, target: str) -> dict:
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "approve_user",
                    AuditLog.target_username == target,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    return json.loads(rows[0].details or "{}")


@pytest.mark.requirement("users:R13")
@pytest.mark.requirement("users:R61")
@pytest.mark.asyncio
async def test_approval_emails_user_with_email(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await _create_pending_user(db_session, "pat", email="pat@example.com")

    resp = await client.post("/v1/users/by_id/pat/approve", headers=auth(admin_token))
    assert resp.status_code == 200

    assert len(ConsoleEmailSender.outbox) == 1
    sent = ConsoleEmailSender.outbox[0]
    assert sent.to == "pat@example.com"
    assert "approved" in sent.subject.lower()

    details = await _approve_audit_details(db_session, "pat")
    assert details["emailSent"] is True


@pytest.mark.requirement("users:R13")
@pytest.mark.asyncio
async def test_approval_without_email_still_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await _create_pending_user(db_session, "pat", email=None)

    resp = await client.post("/v1/users/by_id/pat/approve", headers=auth(admin_token))
    assert resp.status_code == 200
    assert ConsoleEmailSender.outbox == []

    details = await _approve_audit_details(db_session, "pat")
    assert details["emailSent"] is False
