"""Tests for platform rules that had no proving test (#497).

Each test names the rule it proves in ``docs/platform_requirements.md``.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.user import UserStatus

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
    create_user_with_status,
)

PREFS = "/v1/admin/preferences"
RETENTION = "notification_info_retention_days"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.requirement("platform:R2a")
@pytest.mark.asyncio
async def test_should_list_preferences_in_key_order(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    for key in ("zeta", "alpha", "mid"):
        written = await client.patch(
            f"{PREFS}/{key}", json={"value": 1}, headers=auth(admin)
        )
        assert written.status_code == 200, written.text

    response = await client.get(PREFS, headers=auth(admin))

    assert response.status_code == 200, response.text
    keys = [item["key"] for item in response.json()["items"]]
    assert keys == sorted(keys)
    assert {"alpha", "mid", "zeta"} <= set(keys)


@pytest.mark.requirement("platform:R3a")
@pytest.mark.asyncio
async def test_should_return_default_when_defaulted_preference_was_never_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "key": RETENTION,
        "value": 90,
        "updatedAtUtc": None,
        "updatedBy": None,
    }
    written = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 30}, headers=auth(admin)
    )
    assert written.status_code == 200, written.text
    stored = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))
    assert stored.json()["value"] == 30
    assert isinstance(stored.json()["updatedAtUtc"], int)
    assert stored.json()["updatedBy"] == "admin"


@pytest.mark.requirement("platform:R2b")
@pytest.mark.asyncio
async def test_should_list_default_when_defaulted_preference_was_never_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    written = await client.patch(
        f"{PREFS}/alpha", json={"value": "a"}, headers=auth(admin)
    )
    assert written.status_code == 200, written.text

    response = await client.get(PREFS, headers=auth(admin))

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["key"] for item in items] == ["alpha", RETENTION]
    assert items[1] == {
        "key": RETENTION,
        "value": 90,
        "updatedAtUtc": None,
        "updatedBy": None,
    }
    assert isinstance(items[0]["updatedAtUtc"], int)

    rewritten = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 7}, headers=auth(admin)
    )
    assert rewritten.status_code == 200, rewritten.text
    relisted = await client.get(PREFS, headers=auth(admin))
    retention = [i for i in relisted.json()["items"] if i["key"] == RETENTION]
    assert len(retention) == 1
    assert retention[0]["value"] == 7
    assert retention[0]["updatedBy"] == "admin"


@pytest.mark.requirement("platform:R5a")
@pytest.mark.asyncio
async def test_should_refuse_preferences_when_caller_is_not_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    written = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 30}, headers=auth(super_admin)
    )
    assert written.status_code == 200, written.text
    regular = await create_regular_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    member = await create_member_user(db_session, "milo")
    await db_session.commit()

    refused = await client.get(f"{PREFS}/{RETENTION}", headers=auth(regular))
    assert refused.status_code == 403, refused.text
    for token in (coach, member):
        for method, url, body in (
            ("GET", PREFS, None),
            ("GET", f"{PREFS}/{RETENTION}", None),
            ("PATCH", f"{PREFS}/{RETENTION}", {"value": 5}),
        ):
            response = await client.request(method, url, json=body, headers=auth(token))
            assert response.status_code == 403, (method, url, response.text)
    anonymous = await client.get(f"{PREFS}/{RETENTION}")
    assert anonymous.status_code == 401, anonymous.text

    kept = await client.get(f"{PREFS}/{RETENTION}", headers=auth(super_admin))
    assert kept.json()["value"] == 30


@pytest.mark.requirement("platform:R6")
@pytest.mark.asyncio
async def test_should_store_any_value_when_key_has_no_validation(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    written = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": "forever"}, headers=auth(admin)
    )

    assert written.status_code == 200, written.text
    stored = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))
    assert stored.json()["value"] == "forever"


@pytest.mark.requirement("platform:R7")
@pytest.mark.asyncio
async def test_should_write_one_audit_row_when_preference_is_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    written = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 30}, headers=auth(admin)
    )
    assert written.status_code == 200, written.text

    log = await client.get("/v1/audit_log", headers=auth(admin))
    assert log.status_code == 200, log.text
    assert log.json()["total"] == 1
    assert log.json()["rows"][0]["action"] == "update_system_preference"


@pytest.mark.requirement("platform:R11a")
@pytest.mark.asyncio
async def test_should_refuse_admin_reset_when_caller_is_coach_or_member(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    member = await create_member_user(db_session, "milo")
    _ = await create_member_user(db_session, "tess")
    await db_session.commit()

    for token in (coach, member):
        response = await client.post(
            "/v1/admin/reset-password/tess", headers=auth(token)
        )
        assert response.status_code == 403, response.text

    login = await client.post(
        "/v1/auth/login", json={"username": "tess", "password": "memberpass123"}
    )
    assert login.status_code == 200, login.text


@pytest.mark.requirement("platform:R11a")
@pytest.mark.asyncio
async def test_should_return_404_when_admin_resets_a_soft_deleted_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )
    await db_session.commit()

    response = await client.post("/v1/admin/reset-password/gone", headers=auth(admin))

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("platform:R11b")
@pytest.mark.asyncio
async def test_should_reset_another_admins_password_when_caller_is_admin(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    first = await create_regular_admin_user(db_session, "ra1")
    _ = await create_regular_admin_user(db_session, "ra2")

    response = await client.post("/v1/admin/reset-password/ra2", headers=auth(first))

    assert response.status_code == 200, response.text
    new_password = response.json()["newPassword"]
    login = await client.post(
        "/v1/auth/login", json={"username": "ra2", "password": new_password}
    )
    assert login.status_code == 200, login.text


@pytest.mark.requirement("platform:R14a")
@pytest.mark.asyncio
async def test_should_report_evaluations_and_marketing_off_when_not_enabled(
    client: AsyncClient,
):
    response = await client.get("/v1/capabilities")

    assert response.status_code == 200, response.text
    assert response.json()["evaluations"] is False
    assert response.json()["eventMarketing"] is False


@pytest.mark.requirement("platform:R14a")
@pytest.mark.asyncio
async def test_should_report_evaluations_and_marketing_on_when_enabled(
    client: AsyncClient, evaluations_enabled: None, event_marketing_enabled: None
):
    response = await client.get("/v1/capabilities")

    assert response.status_code == 200, response.text
    assert response.json()["evaluations"] is True
    assert response.json()["eventMarketing"] is True


@pytest.mark.requirement("platform:R23a")
@pytest.mark.asyncio
async def test_should_include_both_ends_when_filtering_by_time_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    for ts in (99, 100, 200, 201):
        db_session.add(
            AuditLog(timestamp=ts, action="login", details=json.dumps({"ts": ts}))
        )
    await db_session.flush()

    response = await client.get(
        "/v1/audit_log", params={"from_ts": 100, "to_ts": 200}, headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    assert [r["timestamp"] for r in response.json()["rows"]] == [200, 100]
