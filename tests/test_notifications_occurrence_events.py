"""Tests for #69 — per-occurrence override notifications.

| Event                    | Recipient        | Category      |
|--------------------------|------------------|---------------|
| occurrence.rescheduled   | enrolled members | informational |
| occurrence.cancelled     | enrolled members | informational |
| occurrence.restored      | enrolled members | informational |
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import create_admin_user
from .redesign_helpers import occurrence_version


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


async def _setup_event_with_enrolled_member(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int, int, int]:
    """Returns (admin_token, event_id, start_ms, venue_id) with one enrolled member 'amy'."""
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "amy")

    venue = await client.post(
        "/v1/venues", json={"name": "V1"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    start_ms = future_ms(24)
    end_ms = future_ms(25)
    event = await client.post(
        "/v1/events",
        json={
            "title": "Practice",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
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

    return admin_token, event_id, start_ms, venue_id


# ---------------------------------------------------------------------------
# occurrence.rescheduled
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R81")
@pytest.mark.asyncio
async def test_reschedule_notifies_enrolled_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token, event_id, start_ms, venue_id = await _setup_event_with_enrolled_member(
        client, db_session
    )
    new_start = future_ms(48)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(
                client, admin_token, event_id, start_ms
            ),
            "newStartTimeUtc": new_start,
            "newVenueId": venue_id,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 204

    rows = await _notifications_for(db_session, "amy", "occurrence.rescheduled")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["occurrenceTimeUtc"] == start_ms
    assert data["changes"]["startTimeUtc"] == new_start
    assert data["changes"]["venueId"] == venue_id


@pytest.mark.requirement("notifications:R81")
@pytest.mark.asyncio
async def test_reschedule_with_no_enrollees_still_notifies_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    """The audience now includes the organizer (here the creator 'admin'), so
    a reschedule notifies them even with no enrolled members (#108/#113)."""
    admin_token = await create_admin_user(db_session)
    venue = await client.post(
        "/v1/venues", json={"name": "V1"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]

    start_ms = future_ms(24)
    end_ms = future_ms(25)
    event = await client.post(
        "/v1/events",
        json={
            "title": "Solo",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(
                client, admin_token, event_id, start_ms
            ),
            "newStartTimeUtc": future_ms(48),
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 204

    rows = await _notifications_for(db_session, "admin", "occurrence.rescheduled")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# occurrence.cancelled
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R82")
@pytest.mark.asyncio
async def test_cancel_notifies_enrolled_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token, event_id, start_ms, _ = await _setup_event_with_enrolled_member(
        client, db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={
            "version": await occurrence_version(
                client, admin_token, event_id, start_ms
            ),
            "reason": "Weather",
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 204

    rows = await _notifications_for(db_session, "amy", "occurrence.cancelled")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["occurrenceTimeUtc"] == start_ms


# ---------------------------------------------------------------------------
# occurrence.restored
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R47")
@pytest.mark.asyncio
async def test_restore_within_grace_unread_deletes_cancellation(
    client: AsyncClient, db_session: AsyncSession
):
    """#111 hybrid policy: an immediate undo of an unread cancellation deletes
    the cancellation notification and fires no restored notification."""
    admin_token, event_id, start_ms, _ = await _setup_event_with_enrolled_member(
        client, db_session
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={
            "version": await occurrence_version(
                client, admin_token, event_id, start_ms
            ),
            "reason": "Weather",
        },
        headers=auth(admin_token),
    )
    assert len(await _notifications_for(db_session, "amy", "occurrence.cancelled")) == 1

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/undo-cancel",
        json={
            "version": await occurrence_version(client, admin_token, event_id, start_ms)
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 204

    assert await _notifications_for(db_session, "amy", "occurrence.cancelled") == []
    assert await _notifications_for(db_session, "amy", "occurrence.restored") == []
