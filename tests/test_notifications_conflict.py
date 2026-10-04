"""Tests for #73 — admin conflict alerts on camp create."""

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


async def _make_camp(
    client: AsyncClient,
    token: str,
    venue_id: int,
    start: int,
    end: int,
    title: str = "Camp",
) -> int:
    response = await client.post(
        "/v1/events",
        json={
            "title": title,
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": end,
        },
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.requirement("notifications:R80")
@pytest.mark.asyncio
async def test_conflicting_camp_creation_notifies_admins(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")

    venue = await client.post(
        "/v1/venues", json={"name": "V1"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    start = future_ms(24)
    end = future_ms(26)
    first_id = await _make_camp(client, admin_token, venue_id, start, end, "First")

    overlap_start = future_ms(25)
    overlap_end = future_ms(27)
    second_id = await _make_camp(
        client, admin_token, venue_id, overlap_start, overlap_end, "Second"
    )

    for admin_name in ("admin", "ra"):
        rows = await _notifications_for(
            db_session, admin_name, "event.conflict_detected"
        )
        assert len(rows) == 1, f"{admin_name} should be notified"
        data: Any = rows[0].payload["data"]
        assert data["eventId"] == second_id
        assert len(data["conflictingEvents"]) == 1
        assert data["conflictingEvents"][0]["eventId"] == first_id


@pytest.mark.requirement("notifications:R80")
@pytest.mark.asyncio
async def test_non_conflicting_camp_is_silent(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)

    venue = await client.post(
        "/v1/venues", json={"name": "V1"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    _ = await _make_camp(
        client, admin_token, venue_id, future_ms(24), future_ms(26), "First"
    )
    _ = await _make_camp(
        client, admin_token, venue_id, future_ms(48), future_ms(50), "Second"
    )

    rows = await _notifications_for(db_session, "admin", "event.conflict_detected")
    assert rows == []
