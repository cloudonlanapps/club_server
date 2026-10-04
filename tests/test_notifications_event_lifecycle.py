"""Tests for #70 — event soft-delete / restore / split notifications.

| Event           | Recipient        | Category      |
|-----------------|------------------|---------------|
| event.deleted   | enrolled members | informational |
| event.restored  | enrolled members | informational |
| event.split     | propagated enrollees | informational |
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import create_admin_user, create_coach_user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_ms(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


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


async def _register_member(client: AsyncClient, admin_token: str, username: str) -> str:
    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "pw123",
            "firstName": "Test",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    _ = await client.post(
        f"/v1/users/by_id/{username}/approve", headers=auth(admin_token)
    )
    login = await client.post(
        "/v1/auth/login", json={"username": username, "password": "pw123"}
    )
    return login.json()["accessToken"]


async def _setup_event_with_member(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    event_type: str = "programme",
) -> tuple[str, int]:
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "amy")

    venue = await client.post(
        "/v1/venues", json={"name": "V1"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]
    event = await client.post(
        "/v1/events",
        json={
            "title": "Practice",
            "type": event_type,
            "venueId": venue_id,
            "startTimeUtc": future_ms(24),
            "endTimeUtc": future_ms(25),
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]

    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(admin_token),
    )
    assert assign.status_code in (200, 201, 204), assign.text

    return admin_token, event_id


# ---------------------------------------------------------------------------
# event.deleted / event.restored
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R73")
@pytest.mark.asyncio
async def test_soft_delete_notifies_enrolled_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token, event_id = await _setup_event_with_member(client, db_session)

    response = await client.delete(
        f"/v1/events/by_id/{event_id}", headers=auth(admin_token)
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "event.deleted")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert "deletedAtUtc" in data


@pytest.mark.requirement("notifications:R74")
@pytest.mark.asyncio
async def test_restore_notifies_enrolled_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token, event_id = await _setup_event_with_member(client, db_session)

    _ = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(admin_token))
    response = await client.post(
        f"/v1/events/by_id/{event_id}/restore", headers=auth(admin_token)
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "event.restored")
    assert len(rows) == 1
    assert rows[0].payload["data"]["eventId"] == event_id


# ---------------------------------------------------------------------------
# event.split
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R79")
@pytest.mark.asyncio
async def test_split_notifies_enrollees(client: AsyncClient, db_session: AsyncSession):
    admin_token, event_id = await _setup_event_with_member(client, db_session)
    _ = await create_coach_user(db_session, "new_coach")

    fetched = await client.get(
        f"/v1/events/by_id/{event_id}", headers=auth(admin_token)
    )
    cutoff = fetched.json()["startTimeUtc"]
    response = await client.patch(
        f"/v1/events/by_id/{event_id}/future",
        json={
            "effectiveDateTimeUtc": cutoff,
            "coachNames": ["new_coach"],
            "version": 1,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == event_id

    rows = await _notifications_for(db_session, "amy", "event.split")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["cutoffTimeUtc"] == cutoff
