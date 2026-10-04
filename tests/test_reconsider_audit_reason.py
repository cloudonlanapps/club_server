"""Tests for #245 — rejection reason captured in the audit log.

``POST /v1/users/by_id/{u}/reconsider`` must record the rejection
``reason`` in the audit-log entry's ``details`` (previously ``null``), so
the reason stays recoverable via ``GET /v1/audit_log`` after the user has
reapplied and the active ``user_review_requests`` row is closed.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_registered_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_pending_user(
    db_session: AsyncSession, client: AsyncClient, username: str = "rosa"
) -> str:
    token = await create_registered_user(db_session, username)
    await attach_identity_document(db_session, username)
    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200, response.text
    return token


@pytest.mark.requirement("users:R61")
@pytest.mark.asyncio
async def test_reconsider_audit_log_captures_reason(
    client: AsyncClient, db_session: AsyncSession
):
    """Regression for #245: the audit-log ``details`` for ``reconsider_user``
    must carry the rejection ``reason`` (was ``null``)."""
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "Please re-upload your ID, the image was blurry."},
    )
    assert response.status_code == 201, response.text

    db_session.expire_all()
    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "reconsider_user",
                AuditLog.target_username == "rosa",
            )
        )
    ).scalar_one()
    assert entry.details is not None
    details = json.loads(entry.details)
    assert details["reason"] == "Please re-upload your ID, the image was blurry."
