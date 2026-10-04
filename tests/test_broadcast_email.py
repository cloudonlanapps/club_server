"""Tests for #271 — optional email delivery of broadcasts via the broadcast
provider, triggered by the per-broadcast ``email=true`` flag.

This is an explicit admin override, independent of #56's preference-driven
dispatch: recipients with an email get it regardless of any per-user toggle;
recipients without an email are skipped, not errored; and email failures never
affect the in-app fan-out.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.user import User, UserStatus
from club_server.mailer import EmailMessage, SendResult
from club_server.mailer.sender import ConsoleEmailSender
from club_server.services import broadcast as broadcast_module
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


async def _active_user(
    db_session: AsyncSession, username: str, *, email: str | None
) -> None:
    db_session.add(
        User(
            username=username,
            email=email,
            password=AuthService.hash_password("pw12345678"),
            first_name=username.capitalize(),
            status=UserStatus.active.value,
            is_super_admin=0,
            roles=json.dumps({"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await db_session.flush()


async def _broadcast_audit_details(db_session: AsyncSession) -> dict:
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "create_broadcast")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    return json.loads(rows[0].details or "{}")


PAYLOAD = {"v": 1, "type": "broadcast.message", "data": {"text": "Hello"}}


@pytest.mark.requirement("notifications:R129")
@pytest.mark.asyncio
async def test_email_true_sends_to_recipients_with_email(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await _active_user(db_session, "amy", email="amy@example.com")
    await _active_user(db_session, "bob", email="bob@example.com")
    await _active_user(db_session, "cal", email=None)
    ConsoleEmailSender.clear()  # drop any setup noise

    resp = await client.post(
        "/v1/broadcasts",
        headers=auth(admin_token),
        json={
            "audienceSelector": {"kind": "users", "usernames": ["amy", "bob", "cal"]},
            "payload": PAYLOAD,
            "email": True,
            "emailSubject": "Practice cancelled",
            "emailBody": "**No practice** tonight.",
        },
    )
    assert resp.status_code == 201

    # Only the two recipients with an email were sent to.
    assert {m.to for m in ConsoleEmailSender.outbox} == {
        "amy@example.com",
        "bob@example.com",
    }
    sent = ConsoleEmailSender.outbox[0]
    assert sent.subject == "Practice cancelled"
    # emailBody is markdown, rendered to HTML for the email.
    assert "<strong>No practice</strong>" in sent.html

    details = await _broadcast_audit_details(db_session)
    assert details["email"] == {
        "requested": True,
        "sent": 2,
        "skippedNoEmail": 1,
        "failed": 0,
    }


@pytest.mark.requirement("notifications:R130")
@pytest.mark.asyncio
async def test_email_false_sends_nothing(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    await _active_user(db_session, "amy", email="amy@example.com")
    ConsoleEmailSender.clear()

    resp = await client.post(
        "/v1/broadcasts",
        headers=auth(admin_token),
        json={
            "audienceSelector": {"kind": "users", "usernames": ["amy"]},
            "payload": PAYLOAD,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["recipientCount"] == 1
    assert ConsoleEmailSender.outbox == []

    details = await _broadcast_audit_details(db_session)
    assert "email" not in details


@pytest.mark.requirement("notifications:R131")
@pytest.mark.asyncio
async def test_email_true_requires_subject_and_body(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await _active_user(db_session, "amy", email="amy@example.com")

    resp = await client.post(
        "/v1/broadcasts",
        headers=auth(admin_token),
        json={
            "audienceSelector": {"kind": "users", "usernames": ["amy"]},
            "payload": PAYLOAD,
            "email": True,
        },
    )
    assert resp.status_code == 422


@pytest.mark.requirement("notifications:R132")
@pytest.mark.asyncio
async def test_email_failure_is_counted_and_does_not_block_fanout(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    admin_token = await create_admin_user(db_session)
    await _active_user(db_session, "amy", email="amy@example.com")
    await _active_user(db_session, "bob", email="bob@example.com")

    class _FailingSender:
        name = "failing"

        async def send(self, _message: EmailMessage) -> SendResult:
            return SendResult(success=False, provider="failing", error="boom")

    monkeypatch.setattr(
        broadcast_module, "get_broadcast_sender", lambda: _FailingSender()
    )

    resp = await client.post(
        "/v1/broadcasts",
        headers=auth(admin_token),
        json={
            "audienceSelector": {"kind": "users", "usernames": ["amy", "bob"]},
            "payload": PAYLOAD,
            "email": True,
            "emailSubject": "Hi",
            "emailBody": "<p>Hi</p>",
        },
    )
    assert resp.status_code == 201
    # In-app fan-out is unaffected by the email failure.
    assert resp.json()["recipientCount"] == 2

    details = await _broadcast_audit_details(db_session)
    assert details["email"] == {
        "requested": True,
        "sent": 0,
        "skippedNoEmail": 0,
        "failed": 2,
    }
