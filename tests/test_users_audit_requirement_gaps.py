"""Tests for users rules that had no proving test (#495): audit.

Each test names the rule it proves in ``docs/users_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .helpers import (
    create_admin_user,
    create_member_user,
    create_regular_admin_user,
    create_user_with_status,
)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _rows(client: AsyncClient, token: str, action: str) -> list[dict]:
    response = await client.get(
        "/v1/audit_log", params={"action": action}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def _actor_target(rows: list[dict]) -> list[tuple[str, str]]:
    return [(r["actor"]["username"], r["target"]["username"]) for r in rows]


@pytest.mark.requirement("users:R62")
@pytest.mark.asyncio
async def test_should_audit_each_account_change_with_actor_and_target(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "tess")
    base = "/v1/users/by_id/tess"
    steps = [
        ("PATCH", base, {"bio": "Goalie"}),
        ("POST", f"{base}/block", None),
        ("POST", f"{base}/unblock", None),
        ("POST", f"{base}/roles", {"role": "coach"}),
        ("DELETE", f"{base}/roles/coach", None),
        ("POST", f"{base}/mark-left", None),
        ("POST", f"{base}/reactivate", None),
        ("DELETE", base, None),
        ("POST", f"{base}/restore", None),
    ]
    for method, url, body in steps:
        response = await client.request(method, url, json=body, headers=auth(admin))
        assert response.status_code == 200, (method, url, response.text)

    for action in (
        "update_user",
        "block_user",
        "unblock_user",
        "assign_role",
        "remove_role",
        "mark_left",
        "reactivate_user",
        "delete_user",
        "restore_user",
    ):
        assert _actor_target(await _rows(client, admin, action)) == [
            ("admin", "tess")
        ], action


@pytest.mark.requirement("users:R62")
@pytest.mark.asyncio
async def test_should_audit_creation_deletion_and_handover_with_actor_and_target(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    heir = await create_regular_admin_user(db_session, "ra")
    _ = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )

    created = await client.post(
        "/v1/users",
        json={
            "username": "newbie",
            "passwordHash": "SecurePass123",
            "firstName": "New",
            "gender": "female",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
        headers=auth(admin),
    )
    assert created.status_code == 201, created.text
    removed = await client.delete("/v1/users/by_id/gone/hard", headers=auth(admin))
    assert removed.status_code == 204, removed.text
    handed = await client.post(
        "/v1/users/by_id/ra/transfer-superadmin", headers=auth(admin)
    )
    assert handed.status_code == 200, handed.text

    # The heir now holds the standing, so reads the whole log.
    assert _actor_target(await _rows(client, heir, "create_user")) == [
        ("admin", "newbie")
    ]
    assert _actor_target(await _rows(client, heir, "hard_delete_user")) == [
        ("admin", "gone")
    ]
    assert _actor_target(await _rows(client, heir, "transfer_superadmin")) == [
        ("admin", "ra")
    ]
