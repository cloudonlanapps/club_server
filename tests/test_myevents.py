from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user, create_coach_user


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
    _pre_login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    _pre_token = _pre_login.json()["accessToken"]
    await attach_identity_document(db_session, username)
    _ = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {_pre_token}"},
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


def future_time_ms(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


async def create_venue(
    client: AsyncClient, token: str, name: str = "Test Venue"
) -> int:
    """Helper to create a venue and return its ID."""
    response = await client.post(
        "/v1/venues",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


async def create_event_and_enroll(
    client: AsyncClient, admin_token: str, username: str
) -> tuple[int, int]:
    """Helper to create an event, enroll a user, and return event_id and start_time."""
    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    end_time = future_time_ms(25)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    return event_id, start_time


async def create_public_event(
    client: AsyncClient,
    admin_token: str,
    venue_name: str = "Public Venue",
    hours_ahead: int = 48,
) -> tuple[int, int]:
    """Helper to create a public event without enrollment. Returns (event_id, start_time)."""
    venue_id = await create_venue(client, admin_token, venue_name)
    start_time = future_time_ms(hours_ahead)
    end_time = future_time_ms(hours_ahead + 1)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Public Event",
            "type": "programme",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return response.json()["id"], start_time


async def create_private_event(
    client: AsyncClient,
    admin_token: str,
    venue_name: str = "Private Venue",
    hours_ahead: int = 48,
) -> tuple[int, int]:
    """Helper to create a private event without enrollment. Returns (event_id, start_time)."""
    venue_id = await create_venue(client, admin_token, venue_name)
    start_time = future_time_ms(hours_ahead)
    end_time = future_time_ms(hours_ahead + 1)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Private Event",
            "type": "programme",
            "visibility": "private",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return response.json()["id"], start_time


# =============================================================================
# Authorization Tests
# =============================================================================


@pytest.mark.asyncio
async def test_user_can_access_own_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a user can access their own events."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == event_id


@pytest.mark.asyncio
async def test_admin_can_access_user_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that admin can access any user's events."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_coach_can_access_user_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that coach can access any user's events."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_access_others_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot access another user's events."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    await create_event_and_enroll(client, admin_token, "user2")

    response = await client.get(
        "/v1/myevents/by_id/user2",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


# =============================================================================
# Query Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_user_event(client: AsyncClient, db_session: AsyncSession):
    """Test getting a single event for a user."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == event_id


@pytest.mark.asyncio
async def test_get_user_enrollment(client: AsyncClient, db_session: AsyncSession):
    """Test getting user's enrollment details."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["membername"] == "testuser"
    assert response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_list_user_occurrences(client: AsyncClient, db_session: AsyncSession):
    """Test listing occurrences for a user."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    await create_event_and_enroll(client, admin_token, "testuser")

    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    future = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/occurrences?fromTimeUtc={now}&toTimeUtc={future}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.asyncio
async def test_get_user_occurrence(client: AsyncClient, db_session: AsyncSession):
    """Test getting a specific occurrence for a user."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, start_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["eventId"] == event_id


@pytest.mark.asyncio
async def test_get_user_attendance_no_record(
    client: AsyncClient, db_session: AsyncSession
):
    """Test getting user attendance when no record exists."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, start_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
async def test_get_user_attendance_with_record(
    client: AsyncClient, db_session: AsyncSession
):
    """Test getting user attendance after it's been marked."""
    from sqlalchemy import select
    from club_server.db.models.event import Event
    from club_server.db.models.enrollment import Enrollment

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _orig_start = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    # Marking attendance requires the open window — move the event into the
    # recent past since super-admin no longer bypasses check_open_window (#106).
    past_start = int(
        (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000
    )
    event_row = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event_row.start_time = past_start
    event_row.end_time = past_start + 60 * 60 * 1000
    enrollment_row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == "testuser",
            )
        )
    ).scalar_one()
    enrollment_row.enrolled_at = past_start - 1
    await db_session.commit()
    start_time = past_start

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["membername"] == "testuser"
    assert response.json()["status"] == "present"


@pytest.mark.asyncio
async def test_list_user_attendance(client: AsyncClient, db_session: AsyncSession):
    """Test listing attendance records for a user."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    future = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={now}&toTimeUtc={future}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# =============================================================================
# Enrollment Mutation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_accept_invite(client: AsyncClient, db_session: AsyncSession):
    """Test accepting an invitation via myevents."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    enrollment_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert enrollment_response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_decline_invite(client: AsyncClient, db_session: AsyncSession):
    """Test declining an invitation via myevents."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/decline",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_request_enrollment(client: AsyncClient, db_session: AsyncSession):
    """Test requesting enrollment via myevents."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    enrollment_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert enrollment_response.json()["status"] == "requested"


@pytest.mark.asyncio
async def test_withdraw_and_cancel_withdraw(
    client: AsyncClient, db_session: AsyncSession
):
    """Test withdrawal request and cancellation via myevents."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Schedule conflict"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    enrollment_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert enrollment_response.json()["status"] == "withdrawRequested"

    cancel_response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/cancel-withdraw",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert cancel_response.status_code == 204

    enrollment_response2 = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert enrollment_response2.json()["status"] == "assigned"


# =============================================================================
# Leave Mutation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_request_and_cancel_leave(client: AsyncClient, db_session: AsyncSession):
    """Test requesting leave and cancelling it via myevents."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, start_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Medical appointment"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    attendance_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert attendance_response.json()["status"] == "onLeaveRequested"

    cancel_response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/cancel",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert cancel_response.status_code == 204

    attendance_response2 = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert attendance_response2.json() is None


@pytest.mark.asyncio
async def test_leave_already_declared_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that declaring leave twice fails."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, start_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Another reason"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LEAVE_ALREADY_DECLARED"


@pytest.mark.asyncio
async def test_get_event_schedules_via_myevents(
    client: AsyncClient, db_session: AsyncSession
):
    """Test getting event chain via myevents."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/schedules",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1
    assert response.json()[0]["eventId"] == event_id


# =============================================================================
# Public Event Visibility Tests (#139)
# =============================================================================


@pytest.mark.asyncio
async def test_list_user_events_includes_public_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Public events appear in user's event list even without enrollment."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, _ = await create_public_event(client, admin_token)

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event_ids = [e["id"] for e in response.json()["items"]]
    assert public_event_id in event_ids


@pytest.mark.asyncio
async def test_list_user_events_includes_enrolled_and_public(
    client: AsyncClient, db_session: AsyncSession
):
    """Both enrolled private events and public events appear in user's list."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    private_event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")
    public_event_id, _ = await create_public_event(client, admin_token)

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event_ids = [e["id"] for e in response.json()["items"]]
    assert private_event_id in event_ids
    assert public_event_id in event_ids
    assert response.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_user_events_no_duplicate_when_enrolled_in_public(
    client: AsyncClient, db_session: AsyncSession
):
    """Public event the user is enrolled in appears exactly once."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, _ = await create_public_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{public_event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event_ids = [e["id"] for e in response.json()["items"]]
    assert event_ids.count(public_event_id) == 1
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_user_events_excludes_private_unenrolled(
    client: AsyncClient, db_session: AsyncSession
):
    """Private events the user is not enrolled in do not appear."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    private_event_id, _ = await create_private_event(client, admin_token)

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event_ids = [e["id"] for e in response.json()["items"]]
    assert private_event_id not in event_ids


@pytest.mark.asyncio
async def test_list_user_events_deleted_public_event_excluded(
    client: AsyncClient, db_session: AsyncSession
):
    """Soft-deleted public events do not appear in user's list."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, _ = await create_public_event(client, admin_token)

    _ = await client.delete(
        f"/v1/events/by_id/{public_event_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/v1/myevents/by_id/testuser",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    event_ids = [e["id"] for e in response.json()["items"]]
    assert public_event_id not in event_ids


@pytest.mark.asyncio
async def test_list_user_occurrences_includes_public_event_occurrences(
    client: AsyncClient, db_session: AsyncSession
):
    """Public event occurrences appear for unenrolled users."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, start_time = await create_public_event(client, admin_token)

    from_time = future_time_ms(0)
    to_time = future_time_ms(48)
    response = await client.get(
        "/v1/myevents/by_id/testuser/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occ_event_ids = [o["eventId"] for o in response.json()]
    assert public_event_id in occ_event_ids


@pytest.mark.asyncio
async def test_list_user_occurrences_excludes_ineligible_public_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Public event whose structured eligibility (gender) doesn't match the
    user must not yield occurrences if the user has no enrollment.

    Mirrors the filter in `/myevents/by_id/{username}` — see issue #117.
    """
    admin_token = await create_admin_user(db_session)
    # `create_user_and_get_token` registers the user as gender="male".
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    venue_id = await create_venue(client, admin_token, "Female Only Venue")
    start_time = future_time_ms(48)
    end_time = future_time_ms(49)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Female Only Public Event",
            "type": "programme",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
            "gender": "female",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert event_response.status_code == 201, event_response.text
    ineligible_event_id = event_response.json()["id"]

    from_time = future_time_ms(0)
    to_time = future_time_ms(72)
    response = await client.get(
        "/v1/myevents/by_id/testuser/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occ_event_ids = [o["eventId"] for o in response.json()]
    assert ineligible_event_id not in occ_event_ids


@pytest.mark.asyncio
async def test_list_user_occurrences_includes_ineligible_public_event_when_enrolled(
    client: AsyncClient, db_session: AsyncSession
):
    """Grandfathering: an enrollment (even on an ineligible public event)
    keeps that event's occurrences visible. See issue #117."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    venue_id = await create_venue(client, admin_token, "Grandfathered Venue")
    start_time = future_time_ms(48)
    end_time = future_time_ms(49)
    # Create the event without an eligibility gate so the user can enroll;
    # then add the gender gate to simulate post-hoc tightening.
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Public Event",
            "type": "oneOff",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert event_response.status_code == 201, event_response.text
    event_id = event_response.json()["id"]

    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert assign.status_code in (200, 204), assign.text

    patch = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"gender": "female", "version": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert patch.status_code == 200, patch.text

    from_time = future_time_ms(0)
    to_time = future_time_ms(72)
    response = await client.get(
        "/v1/myevents/by_id/testuser/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occ_event_ids = [o["eventId"] for o in response.json()]
    assert event_id in occ_event_ids


@pytest.mark.asyncio
async def test_list_user_occurrences_excludes_private_unenrolled(
    client: AsyncClient, db_session: AsyncSession
):
    """Private event occurrences do not appear for unenrolled users."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    private_event_id, _ = await create_private_event(client, admin_token)

    from_time = future_time_ms(0)
    to_time = future_time_ms(48)
    response = await client.get(
        "/v1/myevents/by_id/testuser/occurrences",
        params={"fromTimeUtc": from_time, "toTimeUtc": to_time},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occ_event_ids = [o["eventId"] for o in response.json()]
    assert private_event_id not in occ_event_ids


@pytest.mark.asyncio
async def test_get_user_event_public_without_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """User can access a public event by ID without enrollment."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, _ = await create_public_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{public_event_id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == public_event_id


@pytest.mark.asyncio
async def test_get_user_event_private_enrolled(
    client: AsyncClient, db_session: AsyncSession
):
    """User can access a private event they are enrolled in."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _ = await create_event_and_enroll(client, admin_token, "testuser")

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == event_id


@pytest.mark.asyncio
async def test_get_user_event_private_not_enrolled_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Private event returns 404 for unenrolled user (not 403)."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    private_event_id, _ = await create_private_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{private_event_id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_user_event_schedules_public_without_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """User can access event chain for a public event without enrollment."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, _ = await create_public_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{public_event_id}/schedules",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.asyncio
async def test_get_user_event_schedules_private_not_enrolled_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Private event chain returns 404 for unenrolled user."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    private_event_id, _ = await create_private_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{private_event_id}/schedules",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_user_occurrence_public_without_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """User can access occurrence of a public event without enrollment."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    public_event_id, start_time = await create_public_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{public_event_id}/occurrences/{start_time}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["eventId"] == public_event_id


@pytest.mark.asyncio
async def test_get_user_occurrence_private_not_enrolled_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Private event occurrence returns 404 for unenrolled user."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    private_event_id, start_time = await create_private_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{private_event_id}/occurrences/{start_time}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404


# ============================================================================
# Temporal eligibility for past occurrences (#156). A user who was once
# enrolled and later withdrawn/removed must still see past occurrences they
# participated in. A user removed before the occurrence time must not see it.
# Future occurrences continue to filter by current active enrollment status.
# ============================================================================


def past_time_ms(hours: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)


async def _create_event_in_past(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    title: str = "Past Series",
) -> int:
    """Create a future event then move its start/end to the past via DB."""
    from sqlalchemy import select
    from club_server.db.models.event import Event

    venue_id = await create_venue(client, admin_token, name=f"V-{title}")
    response = await client.post(
        "/v1/events",
        json={
            "title": title,
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = response.json()["id"]
    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(48)
    event.end_time = past_time_ms(47)
    await db_session.flush()
    return event_id


async def _set_enrollment_timestamps(
    db_session: AsyncSession,
    event_id: int,
    membername: str,
    *,
    enrolled_at: int | None,
    withdrawn_at: int | None,
    status: str | None = None,
) -> None:
    from sqlalchemy import select
    from club_server.db.models.enrollment import Enrollment

    result = await db_session.execute(
        select(Enrollment).where(
            Enrollment.event_id == event_id,
            Enrollment.membername == membername,
        )
    )
    enrollment = result.scalar_one()
    enrollment.enrolled_at = enrolled_at
    enrollment.withdrawn_at = withdrawn_at
    if status is not None:
        enrollment.status = status
    await db_session.flush()


async def _enroll_via_assign(
    client: AsyncClient, admin_token: str, event_id: int, membername: str
) -> None:
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [membername]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code in (204, 422), response.text


@pytest.mark.asyncio
async def test_past_occurrence_visible_to_later_removed_member(
    client: AsyncClient, db_session: AsyncSession
):
    """User assigned, attended a past occurrence, then later removed.
    Past occurrence must remain visible in their history."""
    from club_server.db.models.enrollment import EnrollmentStatus

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )

    # Create future event, assign alice — must use a SUPER admin path for
    # subsequent past-event enrollment edits, but here we only need the row.
    event_id, _ = await create_event_and_enroll(client, admin_token, "alice")
    # Move event into the past.
    from sqlalchemy import select
    from club_server.db.models.event import Event

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(48)
    event.end_time = past_time_ms(47)
    await db_session.flush()

    # Mark her removed AFTER the (now-past) occurrence: enrolled before, removed after.
    await _set_enrollment_timestamps(
        db_session,
        event_id,
        "alice",
        enrolled_at=past_time_ms(72),
        withdrawn_at=past_time_ms(24),
        status=EnrollmentStatus.removed.value,
    )

    from_t = past_time_ms(96)
    to_t = future_time_ms(1)
    response = await client.get(
        f"/v1/myevents/by_id/alice/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occurrences = response.json()
    matching = [o for o in occurrences if o["eventId"] == event_id]
    assert len(matching) == 1, f"Expected past occurrence visible: {occurrences}"


@pytest.mark.asyncio
async def test_past_occurrence_hidden_when_removed_before_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """User removed BEFORE the occurrence time must not see that past occurrence."""
    from club_server.db.models.enrollment import EnrollmentStatus

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(client, admin_token, "bob", db_session)

    event_id, _ = await create_event_and_enroll(client, admin_token, "bob")
    from sqlalchemy import select
    from club_server.db.models.event import Event

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(24)
    event.end_time = past_time_ms(23)
    await db_session.flush()

    # Removed 48 hours ago, occurrence was 24 hours ago.
    await _set_enrollment_timestamps(
        db_session,
        event_id,
        "bob",
        enrolled_at=past_time_ms(96),
        withdrawn_at=past_time_ms(48),
        status=EnrollmentStatus.removed.value,
    )

    from_t = past_time_ms(72)
    to_t = future_time_ms(1)
    response = await client.get(
        f"/v1/myevents/by_id/bob/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occurrences = response.json()
    matching = [o for o in occurrences if o["eventId"] == event_id]
    assert matching == [], f"Expected occurrence hidden: {occurrences}"


@pytest.mark.asyncio
async def test_future_occurrence_hidden_for_removed_member(
    client: AsyncClient, db_session: AsyncSession
):
    """A removed user should not see future occurrences (current-status filter)."""
    from club_server.db.models.enrollment import EnrollmentStatus

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "carol", db_session
    )

    event_id, start_time = await create_event_and_enroll(client, admin_token, "carol")
    # Mark removed (terminal status) without changing event times — event stays in the future.
    await _set_enrollment_timestamps(
        db_session,
        event_id,
        "carol",
        enrolled_at=past_time_ms(1),
        withdrawn_at=past_time_ms(0) + 1,  # any past time
        status=EnrollmentStatus.removed.value,
    )

    from_t = past_time_ms(1)
    to_t = future_time_ms(48)
    response = await client.get(
        f"/v1/myevents/by_id/carol/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occurrences = response.json()
    matching = [o for o in occurrences if o["eventId"] == event_id]
    assert matching == [], f"Expected future occurrence hidden: {occurrences}"


@pytest.mark.asyncio
async def test_past_occurrence_hidden_when_invitation_never_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    """An invited-but-never-enrolled user must not see past occurrences."""
    from club_server.db.models.enrollment import EnrollmentStatus

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "dave", db_session
    )

    event_id = await _create_event_in_past(client, db_session, admin_token, "Invited")
    # Insert a declined enrollment row directly: enrolled_at None means never participated.
    from club_server.db.models.enrollment import Enrollment
    from club_server.utils import now_utc_ms

    db_session.add(
        Enrollment(
            event_id=event_id,
            membername="dave",
            status=EnrollmentStatus.declined.value,
            created_at=now_utc_ms(),
            updated_at=now_utc_ms(),
            enrolled_at=None,
            withdrawn_at=None,
        )
    )
    await db_session.flush()

    from_t = past_time_ms(96)
    to_t = future_time_ms(1)
    response = await client.get(
        f"/v1/myevents/by_id/dave/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occurrences = response.json()
    matching = [o for o in occurrences if o["eventId"] == event_id]
    assert matching == [], (
        f"Declined invite should not see past occurrence: {occurrences}"
    )


@pytest.mark.asyncio
async def test_mixed_range_returns_correct_per_occurrence_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    """Recurring event spanning past + future: user enrolled-then-withdrawn
    sees only past occurrences inside their participation window."""
    from sqlalchemy import select
    from club_server.db.models.enrollment import EnrollmentStatus
    from club_server.db.models.event import Event

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "erin", db_session
    )

    venue_id = await create_venue(client, admin_token, name="Recurring")
    # Daily recurring event starting 5 days ago, no UNTIL.
    response = await client.post(
        "/v1/events",
        json={
            "title": "Daily",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(1),
            "endTimeUtc": future_time_ms(2),
            "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = response.json()["id"]
    # Move start back 5 days so we have 5 past + several future occurrences.
    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(24 * 5)
    event.end_time = past_time_ms(24 * 5 - 1)
    await db_session.flush()

    # Assign erin (creates enrollment), then mark her withdrawn 2 days ago.
    await _enroll_via_assign(client, admin_token, event_id, "erin")
    await _set_enrollment_timestamps(
        db_session,
        event_id,
        "erin",
        enrolled_at=past_time_ms(24 * 4),  # joined 4 days ago
        withdrawn_at=past_time_ms(24 * 2),  # left 2 days ago
        status=EnrollmentStatus.withdrawn.value,
    )

    from_t = past_time_ms(24 * 6)
    to_t = future_time_ms(24 * 3)
    response = await client.get(
        f"/v1/myevents/by_id/erin/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    occurrences = [o for o in response.json() if o["eventId"] == event_id]
    # Visible past occurrences: those with enrolled_at <= T <= withdrawn_at.
    # That means days 4 down to 2 ago — at least one occurrence.
    assert len(occurrences) >= 1
    # All must be past (terminal status hides future).
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    for occ in occurrences:
        assert occ["occurrenceTimeUtc"] < now
        assert past_time_ms(24 * 4) <= occ["occurrenceTimeUtc"] <= past_time_ms(24 * 2)


# =============================================================================
# attendance_status on /occurrences (#156).
# The list endpoint must carry the user's recorded attendance status so the
# Flutter "My Calendar" view can render it without a second /attendance call.
# =============================================================================


@pytest.mark.asyncio
async def test_occurrences_includes_attendance_status_when_recorded(
    client: AsyncClient, db_session: AsyncSession
):
    """Past occurrence with a recorded attendance row returns that status."""
    from sqlalchemy import select
    from club_server.db.models.event import Event
    from club_server.db.models.enrollment import Enrollment

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _orig_start = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    # Move the event into the recent past so the attendance-marking window is open.
    past_start = past_time_ms(1)
    event_row = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event_row.start_time = past_start
    event_row.end_time = past_start + 60 * 60 * 1000
    enrollment_row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == "testuser",
            )
        )
    ).scalar_one()
    enrollment_row.enrolled_at = past_start - 1
    await db_session.commit()
    start_time = past_start

    mark_resp = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mark_resp.status_code in (200, 201, 204)

    from_t = past_time_ms(48)
    to_t = future_time_ms(1)
    response = await client.get(
        f"/v1/myevents/by_id/testuser/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    matching = [
        o
        for o in response.json()
        if o["eventId"] == event_id and o["occurrenceTimeUtc"] == start_time
    ]
    assert len(matching) == 1, f"Expected one matching occurrence: {response.json()}"
    assert matching[0]["attendanceStatus"] == "present"


@pytest.mark.asyncio
async def test_occurrences_attendance_status_null_when_no_record(
    client: AsyncClient, db_session: AsyncSession
):
    """Past occurrence with no attendance record returns null."""
    from sqlalchemy import select
    from club_server.db.models.event import Event
    from club_server.db.models.enrollment import Enrollment

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, _orig_start = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    past_start = past_time_ms(1)
    event_row = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event_row.start_time = past_start
    event_row.end_time = past_start + 60 * 60 * 1000
    enrollment_row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == "testuser",
            )
        )
    ).scalar_one()
    enrollment_row.enrolled_at = past_start - 1
    await db_session.commit()
    start_time = past_start

    from_t = past_time_ms(48)
    to_t = future_time_ms(1)
    response = await client.get(
        f"/v1/myevents/by_id/testuser/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    matching = [
        o
        for o in response.json()
        if o["eventId"] == event_id and o["occurrenceTimeUtc"] == start_time
    ]
    assert len(matching) == 1, f"Expected one matching occurrence: {response.json()}"
    assert matching[0]["attendanceStatus"] is None


@pytest.mark.asyncio
async def test_occurrences_attendance_status_null_for_future_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """Future occurrence with no leave declared returns null."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, start_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    from_t = past_time_ms(1)
    to_t = future_time_ms(48)
    response = await client.get(
        f"/v1/myevents/by_id/testuser/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    matching = [
        o
        for o in response.json()
        if o["eventId"] == event_id and o["occurrenceTimeUtc"] == start_time
    ]
    assert len(matching) == 1, f"Expected one matching occurrence: {response.json()}"
    assert matching[0]["attendanceStatus"] is None


@pytest.mark.asyncio
async def test_occurrences_attendance_status_reflects_declared_leave(
    client: AsyncClient, db_session: AsyncSession
):
    """Future occurrence with declared leave returns onLeaveRequested."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, start_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    leave_resp = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert leave_resp.status_code == 204

    from_t = past_time_ms(1)
    to_t = future_time_ms(48)
    response = await client.get(
        f"/v1/myevents/by_id/testuser/occurrences?fromTimeUtc={from_t}&toTimeUtc={to_t}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    matching = [
        o
        for o in response.json()
        if o["eventId"] == event_id and o["occurrenceTimeUtc"] == start_time
    ]
    assert len(matching) == 1, f"Expected one matching occurrence: {response.json()}"
    assert matching[0]["attendanceStatus"] == "onLeaveRequested"
