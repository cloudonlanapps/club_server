"""Tests for #50 — event-domain notifications.

Wired events (issue table cross-referenced with the actual code surface):

| Event                | Recipient        | Category      |
|----------------------|------------------|---------------|
| event.venue_changed  | enrolled users   | informational |
| event.rescheduled    | enrolled users   | informational |
| event.coach_changed  | enrolled users   | informational |
| event.cancelled      | enrolled users   | informational |

Events from the issue table that this codebase does **not** support and are
therefore not wired here:
- event.reminder — time-triggered; deferred to follow-up scheduler issue.
- event.announcement — no announcement endpoint exists.
- event.coach_assigned — `Event.coach_names` is a free-text label list, not
  user references, so no per-coach recipient can be resolved. Once events
  carry first-class staff assignments, the coach can be notified directly.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import attach_identity_document, create_admin_user, create_coach_user
from .redesign_helpers import oneoff_occurrence_version, version_of


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_time_ms(hours: int = 24) -> int:
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


async def _register_member(
    client: AsyncClient, admin_token: str, username: str, db_session: AsyncSession
) -> str:
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
    _pre = await client.post(
        "/v1/auth/login", json={"username": username, "password": "pw123"}
    )
    await attach_identity_document(db_session, username)
    _ = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {_pre.json()['accessToken']}"},
    )
    _ = await client.post(
        f"/v1/users/by_id/{username}/approve", headers=auth(admin_token)
    )
    login = await client.post(
        "/v1/auth/login", json={"username": username, "password": "pw123"}
    )
    return login.json()["accessToken"]


async def _create_venue(client: AsyncClient, admin_token: str, name: str = "V") -> int:
    v = await client.post("/v1/venues", json={"name": name}, headers=auth(admin_token))
    return v.json()["id"]


async def _create_event_with_member(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    *,
    username: str = "alice",
) -> tuple[int, int]:
    """Create event + venue, assign one user. Returns (event_id, venue_id)."""
    _ = await _register_member(client, admin_token, username, db_session)
    venue_id = await _create_venue(client, admin_token, name="V1")

    response = await client.post(
        "/v1/events",
        json={
            "title": "E",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers=auth(admin_token),
    )
    event_id = response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    return event_id, venue_id


# ---------------------------------------------------------------------------
# event.venue_changed
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R69")
@pytest.mark.asyncio
async def test_venue_change_notifies_enrolled_users(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    event_id, old_venue_id = await _create_event_with_member(
        client, admin_token, db_session
    )
    new_venue_id = await _create_venue(client, admin_token, name="V2")

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, admin_token, event_id),
            "venueId": new_venue_id,
        },
        headers=auth(admin_token),
    )
    assert resp.status_code == 200

    rows = await _notifications_for(db_session, "alice", "event.venue_changed")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["previousVenueId"] == old_venue_id
    assert data["newVenueId"] == new_venue_id


# ---------------------------------------------------------------------------
# event.rescheduled
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R70")
@pytest.mark.asyncio
async def test_time_change_notifies_enrolled_users_with_diff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    event_id, _ = await _create_event_with_member(client, admin_token, db_session)

    new_start = future_time_ms(48)
    new_end = future_time_ms(49)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, admin_token, event_id),
            "startTimeUtc": new_start,
            "endTimeUtc": new_end,
        },
        headers=auth(admin_token),
    )
    assert resp.status_code == 200

    rows = await _notifications_for(db_session, "alice", "event.rescheduled")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert "start_time" in data["changes"]
    assert data["changes"]["start_time"]["to"] == new_start
    assert "end_time" in data["changes"]


# ---------------------------------------------------------------------------
# event.coach_changed
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R71")
@pytest.mark.asyncio
async def test_coach_change_notifies_enrolled_users(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "carla")
    event_id, _ = await _create_event_with_member(client, admin_token, db_session)

    patch = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"coachNames": ["carla"], "version": 1},
        headers=auth(admin_token),
    )
    assert patch.status_code == 200

    rows = await _notifications_for(db_session, "alice", "event.coach_changed")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["coachNames"] == ["carla"]


@pytest.mark.requirement("notifications:R71")
@pytest.mark.asyncio
async def test_coach_unchanged_emits_nothing(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "carla")
    event_id, _ = await _create_event_with_member(client, admin_token, db_session)

    # First patch sets coaches.
    _ = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"coachNames": ["carla"], "version": 1},
        headers=auth(admin_token),
    )
    # Second patch with the same list — no diff.
    _ = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"coachNames": ["carla"], "version": 2},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "event.coach_changed")
    assert len(rows) == 1, "no second notification when list is identical"


# ---------------------------------------------------------------------------
# event.cancelled
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R82")
@pytest.mark.asyncio
async def test_cancel_notifies_active_enrollments(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    event_id, _ = await _create_event_with_member(
        client, admin_token, db_session, username="alice"
    )
    bob_token = await _register_member(client, admin_token, "bob", db_session)
    _ = bob_token
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["bob"]},
        headers=auth(admin_token),
    )

    cancel = await client.post(
        f"/v1/events/by_id/{event_id}/drop",
        json={
            "version": await oneoff_occurrence_version(client, admin_token, event_id),
            "reason": "venue unavailable",
        },
        headers=auth(admin_token),
    )
    assert cancel.status_code == 200

    for u in ("alice", "bob"):
        rows = await _notifications_for(db_session, u, "occurrence.cancelled")
        assert len(rows) == 1, f"{u} should receive occurrence.cancelled"
        data = rows[0].payload["data"]
        assert data["eventId"] == event_id
        assert data["reason"] == "venue unavailable"
        assert "occurrenceTimeUtc" in data


@pytest.mark.asyncio
async def test_cancel_skips_users_with_no_active_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """Users whose enrollment is in a terminal state at cancel time get no notification."""
    admin_token = await create_admin_user(db_session)
    event_id, _ = await _create_event_with_member(
        client, admin_token, db_session, username="alice"
    )

    # Remove alice before cancelling the event.
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "moved", "effectiveDateTimeUtc": future_time_ms(24)},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "event.cancelled")
    assert rows == []


# ---------------------------------------------------------------------------
# multiple changes in one PATCH fire all applicable types
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R72")
@pytest.mark.asyncio
async def test_multiple_changes_fire_all_applicable_event_types(
    client: AsyncClient, db_session: AsyncSession
):
    """Schedule changes (venue + time) via /reschedule fire venue_changed and
    rescheduled; a coach change via PATCH fires coach_changed (#232)."""
    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "carla")
    event_id, _ = await _create_event_with_member(client, admin_token, db_session)
    new_venue_id = await _create_venue(client, admin_token, name="V2")

    reschedule = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, admin_token, event_id),
            "venueId": new_venue_id,
            "startTimeUtc": future_time_ms(48),
            "endTimeUtc": future_time_ms(49),
        },
        headers=auth(admin_token),
    )
    assert reschedule.status_code == 200

    _ = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"coachNames": ["carla"], "version": 2},
        headers=auth(admin_token),
    )

    venue = await _notifications_for(db_session, "alice", "event.venue_changed")
    rescheduled = await _notifications_for(db_session, "alice", "event.rescheduled")
    coach = await _notifications_for(db_session, "alice", "event.coach_changed")
    assert len(venue) == 1
    assert len(rescheduled) == 1
    assert len(coach) == 1
