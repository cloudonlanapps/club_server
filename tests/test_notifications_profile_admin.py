"""Tests for #29 — profile.changed_by_admin notification."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import create_admin_user, create_member_user


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


@pytest.mark.requirement("notifications:R106")
@pytest.mark.asyncio
async def test_admin_edits_user_profile_notifies_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    response = await client.patch(
        "/v1/users/by_id/amy",
        json={"firstName": "Amelia", "bio": "Captain"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text

    rows = await _notifications_for(db_session, "amy", "profile.changed_by_admin")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["actorUsername"] == "admin"
    assert data["username"] == "amy"
    assert set(data["changedFields"]) == {"firstName", "bio"}


@pytest.mark.requirement("notifications:R106")
@pytest.mark.asyncio
async def test_self_edit_is_silent(client: AsyncClient, db_session: AsyncSession):
    amy_token = await create_member_user(db_session, "amy")

    response = await client.patch(
        "/v1/users/by_id/amy",
        json={"bio": "Self-updated"},
        headers=auth(amy_token),
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "profile.changed_by_admin")
    assert rows == []


@pytest.mark.requirement("notifications:R106")
@pytest.mark.asyncio
async def test_admin_patch_with_no_fields_is_silent(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    response = await client.patch(
        "/v1/users/by_id/amy",
        json={},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "profile.changed_by_admin")
    assert rows == []
