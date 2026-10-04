"""Tests for #71 — notify users when an admin adds them to a group."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import (
    create_admin_user,
    create_member_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _notifications_for(
    db_session: AsyncSession, username: str, event_type: str
) -> list[Notification]:
    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.username == username,
            Notification.type == event_type,
        )
    )
    return list(result.scalars().all())


async def _make_manual(client: AsyncClient, admin_token: str, name: str = "M") -> int:
    response = await client.post(
        "/v1/groups", json={"name": name}, headers=auth(admin_token)
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.requirement("notifications:R52")
@pytest.mark.asyncio
async def test_direct_add_notifies_user(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)

    response = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )
    assert response.status_code in (200, 201, 204), response.text

    rows = await _notifications_for(db_session, "amy", "group.member_added")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["groupId"] == gid
    assert data["addedByUsername"] == "admin"


@pytest.mark.requirement("notifications:R52")
@pytest.mark.asyncio
async def test_bulk_add_notifies_each_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    gid = await _make_manual(client, admin_token)

    response = await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["amy", "bob"]},
        headers=auth(admin_token),
    )
    assert response.status_code in (200, 201)

    for name in ("amy", "bob"):
        rows = await _notifications_for(db_session, name, "group.member_added")
        assert len(rows) == 1, f"{name} should be notified once"
        assert rows[0].payload["data"]["groupId"] == gid


@pytest.mark.requirement("notifications:R53")
@pytest.mark.asyncio
async def test_remove_still_emits_member_removed(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)

    _ = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )
    remove = await client.delete(
        f"/v1/groups/by_id/{gid}/members/amy", headers=auth(admin_token)
    )
    assert remove.status_code in (200, 204)

    rows = await _notifications_for(db_session, "amy", "group.member_removed")
    assert len(rows) == 1
