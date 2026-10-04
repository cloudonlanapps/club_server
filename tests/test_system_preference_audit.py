"""System preference writes are audited (#525).

platform R7: every preference write records one audit row naming the
actor, the key and the previous and new values, with a summary line in
the audit log reader. Values stay unvalidated (R6).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_regular_admin_user
from .redesign_helpers import auth

PREFS = "/v1/admin/preferences"
RETENTION = "notification_info_retention_days"
ACTION = "update_system_preference"


async def _preference_rows(client: AsyncClient, token: str) -> list[dict]:
    log = await client.get(
        "/v1/audit_log", params={"action": ACTION}, headers=auth(token)
    )
    assert log.status_code == 200, log.text
    return log.json()["rows"]


@pytest.mark.requirement("platform:R7")
@pytest.mark.asyncio
async def test_should_audit_key_and_values_when_super_admin_writes_preference(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    written = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 30}, headers=auth(admin)
    )

    assert written.status_code == 200, written.text
    rows = await _preference_rows(client, admin)
    assert len(rows) == 1
    assert rows[0]["actor"]["username"] == "admin"
    assert rows[0]["resource"]["type"] == "system_preference"
    assert rows[0]["resource"]["id"] == RETENTION
    details = rows[0]["details"]
    assert details["key"] == RETENTION
    assert details["previousValue"] is None
    assert details["newValue"] == "30"
    assert f"set system preference {RETENTION}" in rows[0]["summary"]["en"]


@pytest.mark.requirement("platform:R7")
@pytest.mark.asyncio
async def test_should_audit_previous_value_when_preference_is_rewritten(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    first = await client.patch(
        f"{PREFS}/club_info", json={"value": {"name": "Rink Rats"}}, headers=auth(admin)
    )
    assert first.status_code == 200, first.text

    second = await client.patch(
        f"{PREFS}/club_info", json={"value": {"name": "Ice Owls"}}, headers=auth(admin)
    )

    assert second.status_code == 200, second.text
    rows = await _preference_rows(client, admin)
    assert len(rows) == 2
    newest = rows[0]["details"]
    assert newest["key"] == "club_info"
    assert newest["previousValue"] == '{"name":"Rink Rats"}'
    assert newest["newValue"] == '{"name":"Ice Owls"}'


@pytest.mark.requirement("platform:R7")
@pytest.mark.asyncio
async def test_should_truncate_values_when_preference_value_is_long(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    written = await client.patch(
        f"{PREFS}/notes", json={"value": "x" * 1000}, headers=auth(admin)
    )

    assert written.status_code == 200, written.text
    stored = await client.get(f"{PREFS}/notes", headers=auth(admin))
    assert stored.json()["value"] == "x" * 1000
    details = (await _preference_rows(client, admin))[0]["details"]
    assert len(details["newValue"]) <= 200
    assert details["newValue"].endswith("…")


@pytest.mark.requirement("platform:R7")
@pytest.mark.asyncio
async def test_should_write_no_audit_row_when_preference_write_is_refused(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    await db_session.commit()

    refused = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 5}, headers=auth(regular)
    )
    invalid = await client.patch(
        f"{PREFS}/club_info", json={"value": "not an object"}, headers=auth(super_admin)
    )

    assert refused.status_code == 403, refused.text
    assert invalid.status_code == 422, invalid.text
    assert await _preference_rows(client, super_admin) == []
