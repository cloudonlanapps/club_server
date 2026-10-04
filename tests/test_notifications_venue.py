"""Tests for #74 — admin venue.renamed notifications."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import create_admin_user, create_regular_admin_user


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


@pytest.mark.requirement("notifications:R107")
@pytest.mark.asyncio
async def test_rename_with_future_event_notifies_admins(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")

    venue = await client.post(
        "/v1/venues", json={"name": "Old"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    event = await client.post(
        "/v1/events",
        json={
            "title": "Future",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_ms(24),
            "endTimeUtc": future_ms(25),
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]

    response = await client.patch(
        f"/v1/venues/by_id/{venue_id}",
        json={"name": "New"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    for admin_name in ("admin", "ra"):
        rows = await _notifications_for(db_session, admin_name, "venue.renamed")
        assert len(rows) == 1, f"{admin_name} should be notified"
        data: Any = rows[0].payload["data"]
        assert data["venueId"] == venue_id
        assert data["oldName"] == "Old"
        assert data["newName"] == "New"
        assert data["affectedEventIds"] == [event_id]


@pytest.mark.requirement("notifications:R107")
@pytest.mark.asyncio
async def test_rename_with_no_future_events_is_silent(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)

    venue = await client.post(
        "/v1/venues", json={"name": "Old"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    response = await client.patch(
        f"/v1/venues/by_id/{venue_id}",
        json={"name": "New"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "admin", "venue.renamed")
    assert rows == []


@pytest.mark.requirement("notifications:R107")
@pytest.mark.asyncio
async def test_non_name_patch_is_silent(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)

    venue = await client.post(
        "/v1/venues", json={"name": "Old"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    _ = await client.post(
        "/v1/events",
        json={
            "title": "Future",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_ms(24),
            "endTimeUtc": future_ms(25),
        },
        headers=auth(admin_token),
    )

    response = await client.patch(
        f"/v1/venues/by_id/{venue_id}",
        json={"address": "123 Main"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "admin", "venue.renamed")
    assert rows == []
