"""Tests for #28 / #53 — account.password_changed_self and
account.password_changed_by_admin notifications."""

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


@pytest.mark.requirement("notifications:R104")
@pytest.mark.asyncio
async def test_self_change_emits_password_changed_self(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_member_user(db_session, "amy")

    response = await client.post(
        "/v1/auth/change-password",
        json={
            "currentPassword": "memberpass123",
            "newPassword": "newpass456",
        },
        headers=auth(token),
    )
    assert response.status_code == 204

    rows = await _notifications_for(db_session, "amy", "account.password_changed_self")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["username"] == "amy"

    # The admin-driven variant must NOT fire on a self-change.
    admin_rows = await _notifications_for(
        db_session, "amy", "account.password_changed_by_admin"
    )
    assert admin_rows == []


@pytest.mark.requirement("notifications:R8")
@pytest.mark.requirement("notifications:R104")
@pytest.mark.asyncio
async def test_change_password_with_wrong_current_does_not_notify(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_member_user(db_session, "amy")

    response = await client.post(
        "/v1/auth/change-password",
        json={
            "currentPassword": "WRONG",
            "newPassword": "newpass456",
        },
        headers=auth(token),
    )
    assert response.status_code == 401

    for event in (
        "account.password_changed_self",
        "account.password_changed_by_admin",
    ):
        rows = await _notifications_for(db_session, "amy", event)
        assert rows == []


@pytest.mark.requirement("notifications:R104")
@pytest.mark.asyncio
async def test_admin_reset_emits_password_changed_by_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    response = await client.post(
        "/v1/admin/reset-password/amy", headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text

    rows = await _notifications_for(
        db_session, "amy", "account.password_changed_by_admin"
    )
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["username"] == "amy"
    assert data["actorUsername"] == "admin"

    # The self-change variant must NOT fire when an admin resets.
    self_rows = await _notifications_for(
        db_session, "amy", "account.password_changed_self"
    )
    assert self_rows == []
