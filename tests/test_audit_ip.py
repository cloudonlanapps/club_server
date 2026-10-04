"""Tests for #258 — client IP captured on mutating (non-auth) audit actions.

Previously only the 7 auth/admin actions recorded `details.ip_address`. Now
every mutating action does. Verifies end-to-end via a venue create: the IP
lands in the stored `details` and surfaces in the rendered summary's
"from <ip>" clause.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from tests.helpers import create_admin_user


def _auth(token: str, ip: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Forwarded-For": ip}


@pytest.mark.requirement("platform:R16")
@pytest.mark.asyncio
async def test_create_venue_records_client_ip(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)

    resp = await client.post(
        "/v1/venues",
        headers=_auth(token, "7.7.7.7"),
        json={"name": "Rink A"},
    )
    assert resp.status_code == 201, resp.text

    row = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "create_venue")
        )
    ).scalar_one()
    assert row.details is not None
    assert json.loads(row.details)["ip_address"] == "7.7.7.7"


@pytest.mark.requirement("platform:R26")
@pytest.mark.asyncio
async def test_ip_appears_in_summary(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await client.post(
        "/v1/venues", headers=_auth(token, "8.8.8.8"), json={"name": "Rink B"}
    )

    resp = await client.get(
        "/v1/audit_log", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["rows"] if r["action"] == "create_venue")
    assert row["summary"]["en"].endswith("from 8.8.8.8.")
