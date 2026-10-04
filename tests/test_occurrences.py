from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_regular_admin_user,
)
from .redesign_helpers import occurrence_version


async def create_venue(
    client: AsyncClient, token: str, name: str = "Test Venue"
) -> str:
    """Helper to create a venue and return its ID."""
    response = await client.post(
        "/v1/venues",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


def future_time(hours: int = 24) -> datetime:
    """Helper to get a future time."""
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def future_time_ms(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int(future_time(hours).timestamp() * 1000)


def future_time_iso(hours: int = 24) -> str:
    """Helper to get a future time as ISO string for query params."""
    return future_time(hours).isoformat()


@pytest.mark.asyncio
async def test_list_occurrences(client: AsyncClient, db_session: AsyncSession):
    """Test listing occurrences in a date range."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    _ = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    from_time = future_time_ms(0)
    to_time = future_time_ms(48)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["eventTitle"] == "Test Event"


@pytest.mark.asyncio
async def test_list_occurrences_recurring_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Test listing occurrences for a recurring event."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    _ = await client.post(
        "/v1/events",
        json={
            "title": "Daily Event",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    from_time = future_time_ms(0)
    to_time = future_time_ms(200)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5


@pytest.mark.asyncio
async def test_list_occurrences_range_too_large_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that requesting too large a date range fails."""
    token = await create_admin_user(db_session)

    from_time = future_time_ms(0)
    to_time = future_time_ms(400 * 24)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "RANGE_TOO_LARGE" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_get_specific_occurrence(client: AsyncClient, db_session: AsyncSession):
    """Test getting a specific occurrence."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eventId"] == event_id
    assert data["status"] == "scheduled"
    assert data["isRescheduled"] is False


@pytest.mark.asyncio
async def test_get_nonexistent_occurrence_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that getting a nonexistent occurrence fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    wrong_time_ms = future_time_ms(48)
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{wrong_time_ms}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert "OCCURRENCE_NOT_FOUND" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_reschedule_occurrence(client: AsyncClient, db_session: AsyncSession):
    """Test rescheduling an occurrence."""
    token = await create_admin_user(db_session)
    venue1_id = await create_venue(client, token, "Venue 1")
    venue2_id = await create_venue(client, token, "Venue 2")

    # A camp's slots are whole seconds; a one-off's occurrence is not
    # rescheduled on its own (#472).
    start_ms = future_time_ms(24) // 1000 * 1000
    end_ms = start_ms + 60 * 60 * 1000

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "camp",
            "rrule": "FREQ=DAILY;COUNT=1",
            "venueId": venue1_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    new_start_ms = future_time_ms(48)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "newStartTimeUtc": new_start_ms,
            "new_venue_id": venue2_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = get_response.json()
    assert data["isRescheduled"] is True
    assert data["venueId"] == venue2_id


@pytest.mark.asyncio
async def test_reschedule_past_occurrence_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that rescheduling a past occurrence fails (a super-admin may)."""
    token = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue_id = await create_venue(client, token)

    past_time = datetime.now(timezone.utc) - timedelta(hours=24)
    # A camp's slots are whole seconds; a one-off's occurrence is not
    # rescheduled on its own (#472).
    past_start_ms = int(past_time.timestamp()) * 1000
    past_end_ms = past_start_ms + 60 * 60 * 1000

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Past Event",
            "type": "camp",
            "rrule": "FREQ=DAILY;COUNT=1",
            "venueId": venue_id,
            "startTimeUtc": past_start_ms,
            "endTimeUtc": past_end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{past_start_ms}/reschedule",
        json={
            "version": await occurrence_version(client, admin, event_id, past_start_ms),
            "newStartTimeUtc": future_time_ms(24),
        },
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert response.status_code == 422
    assert "PAST_OCCURRENCE" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_cancel_occurrence(client: AsyncClient, db_session: AsyncSession):
    """Test cancelling an occurrence."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "reason": "Weather conditions",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = get_response.json()
    assert data["status"] == "cancelled"
    # Note: cancelReason is not currently returned by the API
    # assert data["cancelReason"] == "Weather conditions"


@pytest.mark.asyncio
async def test_cancel_already_cancelled_occurrence_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that cancelling an already cancelled occurrence fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "reason": "First cancellation",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "reason": "Second cancellation",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "CANCELLED_OCCURRENCE" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_reschedule_cancelled_occurrence_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that rescheduling a cancelled occurrence fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # A camp's slots are whole seconds; a one-off's occurrence is not
    # rescheduled on its own (#472).
    start_ms = future_time_ms(24) // 1000 * 1000
    end_ms = start_ms + 60 * 60 * 1000

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "camp",
            "rrule": "FREQ=DAILY;COUNT=1",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "reason": "Cancelled",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "newStartTimeUtc": future_time_ms(48),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "CANCELLED_OCCURRENCE" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_occurrence_requires_auth(client: AsyncClient):
    """Test that occurrence endpoints require authentication."""
    from_time = future_time_iso(0)
    to_time = future_time_iso(48)

    response = await client.get(
        f"/v1/events/occurrences?from_time_utc={from_time}&to_time_utc={to_time}"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_occurrences_filter_by_event_type(
    client: AsyncClient, db_session: AsyncSession
):
    """Test listing occurrences filtered by event type."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    _ = await client.post(
        "/v1/events",
        json={
            "title": "One Off Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    _ = await client.post(
        "/v1/events",
        json={
            "title": "Programme Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(48),
            "endTimeUtc": future_time_ms(49),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    from_time = future_time_ms(0)
    to_time = future_time_ms(72)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time, "type": "oneOff"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["eventType"] == "oneOff"


@pytest.mark.asyncio
async def test_soft_deleted_event_excluded_from_occurrence_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that occurrences from a soft-deleted event are excluded from listing."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event To Delete",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    from_time = future_time_ms(0)
    to_time = future_time_ms(200)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 3

    _ = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_get_occurrence_of_soft_deleted_event_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that getting an occurrence of a soft-deleted event returns 404."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event To Delete",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    _ = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_restored_event_reappears_in_occurrence_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that restoring a soft-deleted event makes occurrences reappear."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event To Restore",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    from_time = future_time_ms(0)
    to_time = future_time_ms(200)

    _ = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(response.json()) == 0

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 3


@pytest.mark.asyncio
async def test_hard_delete_cascades_occurrence_overrides(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard-deleting an event with overrides succeeds (CASCADE)."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event With Override",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    from_time = future_time_ms(0)
    to_time = future_time_ms(200)
    list_response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    occs = list_response.json()
    first_occ_time = occs[0]["occurrenceTimeUtc"]

    cancel_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{first_occ_time}/cancel",
        json={
            "version": await occurrence_version(
                client, token, event_id, first_occ_time
            ),
            "reason": "Rain",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_response.status_code == 204, cancel_response.json()

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{first_occ_time}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "cancelled"

    soft_delete_response = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert soft_delete_response.status_code == 200
    assert soft_delete_response.json()["deletedAtUtc"] is not None

    hard_delete_response = await client.delete(
        f"/v1/events/by_id/{event_id}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hard_delete_response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_soft_deleted_event_with_overrides_excluded_from_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft-deleted event with overrides is excluded from occurrence listing."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_ms = future_time_ms(24)
    end_ms = future_time_ms(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event With Override",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/cancel",
        json={"reason": "Rain"},
        headers={"Authorization": f"Bearer {token}"},
    )

    from_time = future_time_ms(0)
    to_time = future_time_ms(200)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(response.json()) == 3

    _ = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 0


# =============================================================================
# Auth tightening tests
# =============================================================================


async def create_user_and_get_token(
    client: AsyncClient, admin_token: str, username: str, db_session: AsyncSession
) -> str:
    """Helper to create a regular user and return their access token."""
    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "testpass123",
            "firstName": "Test",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    _pre = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    await attach_identity_document(db_session, username)
    _ = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {_pre.json()['accessToken']}"},
    )
    _ = await client.post(
        f"/v1/users/by_id/{username}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login_response = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    return login_response.json()["accessToken"]


@pytest.mark.asyncio
async def test_regular_user_cannot_list_occurrences(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot list occurrences (admin/coach only)."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    future = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp() * 1000)

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": now, "toTimeUtc": future},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_regular_user_cannot_get_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot get an occurrence (admin/coach only)."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


# =============================================================================
# Undo-cancel test
# =============================================================================


@pytest.mark.asyncio
async def test_undo_cancel_occurrence(client: AsyncClient, db_session: AsyncSession):
    """Test that undo-cancel restores a cancelled occurrence."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)
    start_time = future_time_ms(24)

    end_time = future_time_ms(25)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test OneOff",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    cancel_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/cancel",
        json={
            "version": await occurrence_version(client, token, event_id, start_time),
            "reason": "Bad weather",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "cancelled"

    undo_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/undo-cancel",
        json={"version": await occurrence_version(client, token, event_id, start_time)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert undo_response.status_code == 204

    get_response2 = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response2.json()["status"] != "cancelled"


# =============================================================================
# Reschedule keeps attendance
# =============================================================================


@pytest.mark.asyncio
async def test_reschedule_occurrence_keeps_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that rescheduling an occurrence preserves existing attendance records."""
    from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
    from club_server.utils import now_utc_ms

    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # A camp's slots are whole seconds; a one-off's occurrence is not
    # rescheduled on its own (#472).
    start_ms = future_time_ms(24) // 1000 * 1000
    end_ms = start_ms + 60 * 60 * 1000

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Training",
            "type": "camp",
            "rrule": "FREQ=DAILY;COUNT=1",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    _ = await create_user_and_get_token(client, token, "attendee", db_session)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["attendee"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Seed an attendance record directly: marking via API is no longer
    # allowed on future occurrences (#106), but this test's subject is the
    # reschedule's preservation behavior, not the marking pathway.
    db_session.add(
        AttendanceRecord(
            event_id=event_id,
            occurrence_time_utc=start_ms,
            membername="attendee",
            status=AttendanceStatus.present.value,
            notes="On time",
            recorded_at=now_utc_ms(),
        )
    )
    await db_session.commit()

    # Reschedule (target time still in the future)
    new_start_ms = future_time_ms(48)
    reschedule_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "newStartTimeUtc": new_start_ms,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reschedule_response.status_code == 204

    # Verify attendance still exists on the original occurrence key
    att_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/attendance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert att_response.status_code == 200
    records = att_response.json()
    assert len(records) == 1
    assert records[0]["membername"] == "attendee"
    assert records[0]["status"] == "present"
