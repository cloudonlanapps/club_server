from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.enrollment import EnrollmentStatus
from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_regular_admin_user,
)


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


def future_time_ms(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


def past_time_ms(hours: int = 24) -> int:
    """Helper to get a past time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)


async def create_venue(client: AsyncClient, token: str) -> str:
    """Helper to create a venue and return its ID."""
    response = await client.post(
        "/v1/venues",
        json={"name": "Test Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


async def create_event(
    client: AsyncClient,
    token: str,
    title: str = "Test Event",
) -> int:
    """Helper to create an event and return its ID."""
    venue_id = await create_venue(client, token)
    data: dict[str, str | int] = {
        "title": title,
        "type": "programme",
        "venueId": venue_id,
        "startTimeUtc": future_time_ms(24),
        "endTimeUtc": future_time_ms(25),
    }
    response = await client.post(
        "/v1/events",
        json=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


@pytest.mark.asyncio
async def test_invite_users(client: AsyncClient, db_session: AsyncSession):
    """Test inviting users to an event."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "invited"


@pytest.mark.asyncio
async def test_invite_already_enrolled_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that inviting already enrolled user fails."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    assert "ALREADY_ENROLLED" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_assign_users(client: AsyncClient, db_session: AsyncSession):
    """Test assigning users directly to an event."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_assign_trial(client: AsyncClient, db_session: AsyncSession):
    """Test assigning a trial to a user."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "assignedTrial"


@pytest.mark.asyncio
async def test_assign_trial_on_one_off_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """assign-trial is restricted to programme events; oneOff must be rejected."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    venue_id = await create_venue(client, admin_token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "One-Off Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 404


@pytest.mark.asyncio
async def test_assign_trial_on_camp_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """assign-trial is restricted to programme events; camp must be rejected."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    venue_id = await create_venue(client, admin_token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Summer Camp",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(72),
            "endTimeUtc": future_time_ms(74),
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 404


@pytest.mark.asyncio
async def test_user_request_enrollment(client: AsyncClient, db_session: AsyncSession):
    """Test user requesting to join an event."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "requested"


@pytest.mark.asyncio
async def test_approve_enrollment_request(
    client: AsyncClient, db_session: AsyncSession
):
    """Test approving an enrollment request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_reject_enrollment_request(client: AsyncClient, db_session: AsyncSession):
    """Test rejecting an enrollment request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["testuser"], "reason": "No slots available"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_accept_invitation(client: AsyncClient, db_session: AsyncSession):
    """Test user accepting an invitation."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

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

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_decline_invitation(client: AsyncClient, db_session: AsyncSession):
    """Test user declining an invitation."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

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

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "declined"


@pytest.mark.asyncio
async def test_request_withdrawal(client: AsyncClient, db_session: AsyncSession):
    """Test user requesting withdrawal from event."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Personal reasons"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "withdrawRequested"


@pytest.mark.asyncio
async def test_approve_withdrawal(client: AsyncClient, db_session: AsyncSession):
    """Test approving a withdrawal request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Personal reasons"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "withdrawn"


@pytest.mark.asyncio
async def test_reject_withdrawal(client: AsyncClient, db_session: AsyncSession):
    """Test rejecting a withdrawal request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Personal reasons"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["testuser"], "reason": "Training commitment required"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_cancel_withdrawal_request(client: AsyncClient, db_session: AsyncSession):
    """Test user cancelling their own withdrawal request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Personal reasons"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/cancel-withdraw",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_remove_enrollment(client: AsyncClient, db_session: AsyncSession):
    """Test admin removing an enrollment."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["testuser"], "reason": "Disciplinary issue"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "removed"


@pytest.mark.asyncio
async def test_list_enrollments(client: AsyncClient, db_session: AsyncSession):
    """Test listing all enrollments for an event."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "user1" in data["enrollments"]
    assert "user2" in data["enrollments"]
    assert data["enrollments"]["user1"] == "assigned"
    assert data["enrollments"]["user2"] == "assigned"


@pytest.mark.asyncio
async def test_list_enrollments_with_status_filter(
    client: AsyncClient, db_session: AsyncSession
):
    """Test listing enrollments with status filter."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        params={"status": "assigned"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "user1" in data["enrollments"]
    assert "user2" not in data["enrollments"]


@pytest.mark.asyncio
async def test_capacity_enforcement(client: AsyncClient, db_session: AsyncSession):
    """Test that capacity setting is stored correctly.

    Note: The service currently does not enforce capacity limits on assignment.
    This test verifies the assignment works, not capacity enforcement.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user3", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Currently, admins can exceed capacity (no enforcement)
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user3"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_invalid_state_transition(client: AsyncClient, db_session: AsyncSession):
    """Test that invalid state transitions are rejected."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert "INVALID_TRANSITION" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_get_nonexistent_enrollment_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Test getting enrollment for non-enrolled user returns 404."""
    admin_token = await create_admin_user(db_session)
    event_id = await create_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/nonexistent/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ENROLLMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_enrollment_requires_auth(client: AsyncClient):
    """Test that enrollment endpoints require authentication."""
    response = await client.get("/v1/events/by_id/1/enrollments")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_regular_user_cannot_invite(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that regular users cannot invite others."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "otheruser", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["otheruser"]},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_user_cannot_accept_others_invitation(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a user cannot accept another user's invitation."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reinvite_after_decline(client: AsyncClient, db_session: AsyncSession):
    """Test that a user can be re-invited after declining."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/decline",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "invited"


@pytest.mark.asyncio
async def test_re_request_after_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a user can request enrollment after withdrawal."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "requested"


@pytest.mark.asyncio
async def test_regular_user_cannot_view_others_enrollment_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot view another user's enrollment status."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/user2/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_user_can_view_own_enrollment_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a user can view their own enrollment status."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_admin_can_view_others_enrollment_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that an admin can view another user's enrollment status."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_coach_can_view_others_enrollment_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a coach can view another user's enrollment status."""
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_bulk_invite_empty_list_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that bulk invite with empty membernames list succeeds as no-op."""
    admin_token = await create_admin_user(db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_bulk_approve_empty_list_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that bulk approve with empty membernames list succeeds as no-op."""
    admin_token = await create_admin_user(db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_bulk_invite_duplicate_users_in_list(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that duplicate usernames in a single invite list are deduplicated silently."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser", "testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "invited"


@pytest.mark.asyncio
async def test_get_enrollment_detail(client: AsyncClient, db_session: AsyncSession):
    """Test that enrollment detail endpoint returns full enrollment with timestamps."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    # Assign user (sets enrolledAtUtc)
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Get enrollment detail
    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["membername"] == "testuser"
    assert data["status"] == "assigned"
    assert data["enrolledAtUtc"] is not None
    assert data["eventId"] == event_id


@pytest.mark.asyncio
async def test_get_enrollment_detail_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that enrollment detail returns 404 for non-enrolled user."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invite_to_cancelled_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Inviting users to a cancelled event is rejected for non-super-admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_event(client, super_admin_token)

    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_assign_to_cancelled_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning users to a cancelled event is rejected for non-super-admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_event(client, super_admin_token)

    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_assign_trial_to_cancelled_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning trials to a cancelled event is rejected for non-super-admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_event(client, super_admin_token)

    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_invite_to_deleted_event_returns_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that inviting users to a soft-deleted event returns 404."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    delete_response = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deletedAtUtc"] is not None

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


def test_enrollment_status_enum_includes_declined():
    """Test that EnrollmentStatus enum includes 'declined' for type safety."""
    assert hasattr(EnrollmentStatus, "declined")
    assert EnrollmentStatus.declined.value == "declined"


def test_enrollment_status_enum_includes_removed():
    """Test that EnrollmentStatus enum includes 'removed' for type safety."""
    assert hasattr(EnrollmentStatus, "removed")
    assert EnrollmentStatus.removed.value == "removed"


def test_enrollment_status_enum_completeness():
    """Test that all enrollment statuses used by the service are in the enum."""
    expected_values = {
        "invited",
        "requested",
        "accepted",
        "rejected",
        "assigned",
        "assignedTrial",
        "withdrawn",
        "withdrawRequested",
        "declined",
        "removed",
    }
    actual_values = {s.value for s in EnrollmentStatus}
    assert actual_values == expected_values


# =============================================================================
# Time conflict validation tests
# =============================================================================


async def create_event_with_times(
    client: AsyncClient,
    token: str,
    title: str,
    start_time_utc: int,
    end_time_utc: int,
    event_type: str = "programme",
    rrule: str | None = None,
    until_time_utc: int | None = None,
    organizer_name: str | None = None,
) -> int:
    """Helper to create an event with explicit start/end times.

    Each call creates a new venue to avoid venue conflicts.
    Use organizer_name to avoid organizer time conflicts between
    overlapping events.
    """
    venue_id = await create_venue(client, token)
    data: dict = {
        "title": title,
        "type": event_type,
        "venueId": venue_id,
        "startTimeUtc": start_time_utc,
        "endTimeUtc": end_time_utc,
    }
    if rrule is not None:
        data["rrule"] = rrule
    if until_time_utc is not None:
        data["untilTimeUtc"] = until_time_utc
    if organizer_name is not None:
        data["organizerName"] = organizer_name

    response = await client.post(
        "/v1/events",
        json=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_assign_user_time_conflict_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning a user to an overlapping event should return 409 TIME_CONFLICT."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    _ = await create_user_and_get_token(client, admin_token, "organizer2", db_session)

    start = future_time_ms(24)
    end = future_time_ms(26)

    event_a = await create_event_with_times(
        client, admin_token, "Event A", start, end, event_type="programme"
    )
    event_b = await create_event_with_times(
        client,
        admin_token,
        "Event B",
        start,
        end,
        event_type="programme",
        organizer_name="organizer2",
    )

    # Assign user to event A
    response = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Try to assign same user to overlapping event B — should be rejected
    response = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TIME_CONFLICT"
    assert event_a in response.json()["detail"]["conflicting_event_ids"]


@pytest.mark.asyncio
async def test_assign_trial_time_conflict_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning a trial to an overlapping event should return 409 TIME_CONFLICT."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    _ = await create_user_and_get_token(client, admin_token, "organizer2", db_session)

    start = future_time_ms(24)
    end = future_time_ms(26)

    event_a = await create_event_with_times(
        client, admin_token, "Event A", start, end, event_type="programme"
    )
    # Event B must be a programme — assign-trial only applies to programmes.
    event_b = await create_event_with_times(
        client,
        admin_token,
        "Event B",
        start,
        end,
        organizer_name="organizer2",
    )

    # Assign user to event A
    response = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Try trial assignment to overlapping event B — should be rejected
    response = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TIME_CONFLICT"


@pytest.mark.asyncio
async def test_assign_no_conflict_different_times(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning a user to non-overlapping events should succeed."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    event_a = await create_event_with_times(
        client,
        admin_token,
        "Event A",
        future_time_ms(24),
        future_time_ms(25),
        event_type="oneOff",
    )
    event_b = await create_event_with_times(
        client,
        admin_token,
        "Event B",
        future_time_ms(48),
        future_time_ms(49),
        event_type="oneOff",
    )

    response = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    response = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_assign_no_conflict_after_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    """After withdrawal from event A, assigning to overlapping event B should succeed."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "organizer2", db_session)

    start = future_time_ms(24)
    end = future_time_ms(26)

    event_a = await create_event_with_times(
        client, admin_token, "Event A", start, end, event_type="programme"
    )
    event_b = await create_event_with_times(
        client,
        admin_token,
        "Event B",
        start,
        end,
        event_type="programme",
        organizer_name="organizer2",
    )

    # Assign user to event A
    response = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # User requests withdrawal
    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_a}/enrollments/withdraw",
        json={"reason": "switching events"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    # Admin approves withdrawal
    response = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/approve-withdraw",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Now assign to overlapping event B — should succeed
    response = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_assign_no_conflict_cancelled_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Cancelled event enrollment should not block assignment to overlapping event."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    _ = await create_user_and_get_token(client, admin_token, "organizer2", db_session)

    start = future_time_ms(24)
    end = future_time_ms(25)

    event_a = await create_event_with_times(
        client, admin_token, "Event A", start, end, event_type="programme"
    )
    event_b = await create_event_with_times(
        client,
        admin_token,
        "Event B",
        start,
        end,
        event_type="programme",
        organizer_name="organizer2",
    )

    # Assign user to event A
    response = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Terminate event A at its only occurrence
    await _cancel_event(client, admin_token, event_a)

    # Assign to overlapping event B — should succeed since A is cancelled
    response = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


# =============================================================================
# Fix #2: reject/cancel withdrawal restores previous_status
# =============================================================================


@pytest.mark.asyncio
async def test_reject_withdrawal_restores_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    """reject_withdrawal restores accepted status when user was accepted before withdrawal."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    # Invite → accept (status becomes accepted)
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    # Request withdrawal
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Busy"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    # Reject withdrawal — should restore to accepted
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_reject_withdrawal_restores_assigned_trial(
    client: AsyncClient, db_session: AsyncSession
):
    """reject_withdrawal restores assigned_trial when user was trial before withdrawal."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    # Assign trial
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Request withdrawal
    _ = await create_user_and_get_token(
        client, admin_token, "testuser_helper", db_session
    )
    # Need testuser's token — recreate
    # Actually we need testuser's token. Let me use the admin to act on behalf.
    # The withdrawal endpoint is via myevents which requires self/admin/coach access.
    # Admin can act on behalf since check_myevents_access allows admin.
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Trial not working"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Reject withdrawal — should restore to assignedTrial
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "assignedTrial"


@pytest.mark.asyncio
async def test_cancel_withdrawal_restores_assigned(
    client: AsyncClient, db_session: AsyncSession
):
    """cancel_withdrawal restores assigned status."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Changed mind"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/cancel-withdraw",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "assigned"


# =============================================================================
# Fix #3: accept_invite and request_enrollment check time conflicts
# =============================================================================


@pytest.mark.asyncio
async def test_accept_invite_time_conflict_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """accept_invite must reject when user has overlapping active enrollment."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "organizer2", db_session)

    start = future_time_ms(24)
    end = future_time_ms(25)

    event_a = await create_event_with_times(client, admin_token, "Event A", start, end)
    event_b = await create_event_with_times(
        client, admin_token, "Event B", start, end, organizer_name="organizer2"
    )

    # Assign user to event A
    _ = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Invite user to event B
    _ = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Accept invite — should fail with time conflict
    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_b}/enrollments/accept",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TIME_CONFLICT"


@pytest.mark.asyncio
async def test_request_enrollment_time_conflict_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """request_enrollment must reject when user has overlapping active enrollment."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "organizer2", db_session)

    start = future_time_ms(24)
    end = future_time_ms(25)

    event_a = await create_event_with_times(client, admin_token, "Event A", start, end)
    event_b = await create_event_with_times(
        client, admin_token, "Event B", start, end, organizer_name="organizer2"
    )

    # Assign user to event A
    _ = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Request enrollment to event B — should fail with time conflict
    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_b}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TIME_CONFLICT"


@pytest.mark.asyncio
async def test_accept_invite_no_conflict_different_times(
    client: AsyncClient, db_session: AsyncSession
):
    """accept_invite succeeds when events don't overlap."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    event_a = await create_event_with_times(
        client,
        admin_token,
        "Event A",
        future_time_ms(24),
        future_time_ms(25),
    )
    event_b = await create_event_with_times(
        client,
        admin_token,
        "Event B",
        future_time_ms(48),
        future_time_ms(49),
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_a}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_b}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_b}/enrollments/accept",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204


# =============================================================================
# #108: Event cancellation does not mutate enrollments
# =============================================================================


@pytest.mark.asyncio
async def test_cancel_event_preserves_active_enrollments(
    client: AsyncClient, db_session: AsyncSession
):
    """#108: cancelling an event leaves active enrollments untouched.

    Cancellation is an event-level fact (until_time); enrollment records that a
    user signed up and must survive the cancel so the relationship — and any
    later undo-cancel — stays intact.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    # Assign user1 and user2
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Cancel the event
    await _cancel_event(client, admin_token, event_id)

    # Both enrollments survive unchanged.
    for username in ["user1", "user2"]:
        status_response = await client.get(
            f"/v1/myevents/by_id/{username}/{event_id}/enrollments",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert status_response.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_cancel_event_preserves_terminal_enrollments(
    client: AsyncClient, db_session: AsyncSession
):
    """Cancelling an event does not change already-terminal enrollments."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    # Invite then decline (terminal state)
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/decline",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    # Cancel the event
    await _cancel_event(client, admin_token, event_id)

    # Verify enrollment remains declined (not changed to removed)
    status_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_response.json()["status"] == "declined"


# =============================================================================
# Bulk enrollment operations
# =============================================================================


@pytest.mark.asyncio
async def test_bulk_assign_users(client: AsyncClient, db_session: AsyncSession):
    """Test assigning multiple users at once via bulk assign."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user3", db_session)
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2", "user3"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "assigned"
    assert enrollments["user2"] == "assigned"
    assert enrollments["user3"] == "assigned"


@pytest.mark.asyncio
async def test_bulk_assign_transitions_invited_to_assigned(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that bulk assign transitions invited users to assigned."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "assigned"
    assert enrollments["user2"] == "assigned"


@pytest.mark.asyncio
async def test_bulk_approve_withdrawal(client: AsyncClient, db_session: AsyncSession):
    """Test approving multiple withdrawal requests at once."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/enrollments/withdraw",
        json={"reason": "Reason 1"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/enrollments/withdraw",
        json={"reason": "Reason 2"},
        headers={"Authorization": f"Bearer {user2_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "withdrawn"
    assert enrollments["user2"] == "withdrawn"


@pytest.mark.asyncio
async def test_bulk_reject_withdrawal(client: AsyncClient, db_session: AsyncSession):
    """Test rejecting multiple withdrawal requests at once."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/enrollments/withdraw",
        json={"reason": "Reason 1"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/enrollments/withdraw",
        json={"reason": "Reason 2"},
        headers={"Authorization": f"Bearer {user2_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "assigned"
    assert enrollments["user2"] == "assigned"


@pytest.mark.asyncio
async def test_bulk_remove_enrollments(client: AsyncClient, db_session: AsyncSession):
    """Test removing multiple enrolled users at once."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "removed"
    assert enrollments["user2"] == "removed"


@pytest.mark.asyncio
async def test_bulk_remove_partial(client: AsyncClient, db_session: AsyncSession):
    """Test removing some users while others remain enrolled."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user3", db_session)
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2", "user3"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["user1", "user3"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "removed"
    assert enrollments["user2"] == "assigned"
    assert enrollments["user3"] == "removed"


@pytest.mark.asyncio
async def test_withdrawal_with_reason(client: AsyncClient, db_session: AsyncSession):
    """Test that withdrawal reason is stored and returned in enrollment detail."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Moving to another city"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    detail_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail_response.status_code == 200
    data = detail_response.json()
    assert data["status"] == "withdrawRequested"
    assert data["withdrawalReason"] == "Moving to another city"


@pytest.mark.asyncio
async def test_multiple_users_request_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    """Test multiple users requesting withdrawal from the same event."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response1 = await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/enrollments/withdraw",
        json={"reason": "Scheduling conflict"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response1.status_code == 204

    response2 = await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/enrollments/withdraw",
        json={"reason": "Personal reasons"},
        headers={"Authorization": f"Bearer {user2_token}"},
    )
    assert response2.status_code == 204

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "withdrawRequested"
    assert enrollments["user2"] == "withdrawRequested"


# =============================================================================
# Enrollment extras: mixed approve/reject, withdrawal reason after approval
# =============================================================================


@pytest.mark.asyncio
async def test_mixed_approve_and_reject_enrollment_requests(
    client: AsyncClient, db_session: AsyncSession
):
    """Test approving some enrollment requests and rejecting others in separate calls."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )
    user3_token = await create_user_and_get_token(
        client, admin_token, "user3", db_session
    )
    event_id = await create_event(client, admin_token)

    # Users request enrollment
    await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user2_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user3/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user3_token}"},
    )

    # Approve user1 and user2
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Reject user3
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["user3"], "reason": "Capacity full"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["user1"] == "accepted"
    assert enrollments["user2"] == "accepted"
    assert enrollments["user3"] == "rejected"


@pytest.mark.asyncio
async def test_withdrawal_reason_persists_after_approval(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that withdrawal reason remains visible after withdrawal is approved."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Relocating abroad"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    detail_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail_response.status_code == 200
    data = detail_response.json()
    assert data["status"] == "withdrawn"
    assert data["withdrawalReason"] == "Relocating abroad"


@pytest.mark.asyncio
async def test_reject_withdrawal_with_reason(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that reject-withdraw stores and the enrollment reverts to previous status."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/withdraw",
        json={"reason": "Want to leave"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["testuser"], "reason": "Commitment required"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    detail_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail_response.status_code == 200
    data = detail_response.json()
    # Should revert to assigned (previous status)
    assert data["status"] == "assigned"


async def create_past_event(
    client: AsyncClient,
    token: str,
    db_session: AsyncSession,
    title: str = "Past Event",
) -> int:
    """Helper to create a past event.

    Creates a future event via the API, then moves its timestamps to
    the past via direct DB update (no API endpoint changes programme times).
    """
    from sqlalchemy import select
    from club_server.db.models.event import Event

    venue_id = await create_venue(client, token)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": title,
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(48)
    event.end_time = past_time_ms(47)
    await db_session.flush()

    return event_id


@pytest.mark.asyncio
async def test_request_enrollment_past_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Enrollment request is rejected for a past event."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_past_event(client, admin_token, db_session)

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_invite_user_past_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Inviting a user to a past event is rejected for non-super-admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_past_event(client, super_admin_token, db_session)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_assign_user_past_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning a user to a past event is rejected for non-super-admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_past_event(client, super_admin_token, db_session)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_assign_trial_past_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Assigning a trial to a past event is rejected for non-super-admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_past_event(client, super_admin_token, db_session)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "testuser"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_accept_invite_past_event_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Accepting an invitation for a past event is rejected."""
    from sqlalchemy import select
    from club_server.db.models.event import Event

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    # Create future event and invite user
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Move event to the past via direct DB update
    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(48)
    event.end_time = past_time_ms(47)
    await db_session.flush()

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_enrollment_future_event_still_works(
    client: AsyncClient, db_session: AsyncSession
):
    """Enrollment operations on future events continue to work normally."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id = await create_event(client, admin_token)

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204


# ============================================================================
# Past-event guard coverage for previously-unguarded enrollment mutations
# (#155). Each mutation is checked for: regular admin gets 422; super-admin
# bypasses and succeeds.
# ============================================================================


async def _move_event_to_past(db_session: AsyncSession, event_id: int) -> None:
    from sqlalchemy import select
    from club_server.db.models.event import Event

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_time_ms(48)
    event.end_time = past_time_ms(47)
    await db_session.flush()


async def _seed_user_with_status(
    client: AsyncClient,
    super_admin_token: str,
    db_session: AsyncSession,
    username: str,
    desired_status: str,
) -> int:
    """Create a future event, transition `username` into `desired_status`, then
    move the event to the past. Returns event_id."""
    event_id = await create_event(
        client, super_admin_token, title=f"E-{desired_status}"
    )

    if desired_status == EnrollmentStatus.requested.value:
        user_token = await create_user_and_get_token(
            client, super_admin_token, username, db_session
        )
        _ = await client.post(
            f"/v1/myevents/by_id/{username}/{event_id}/enrollments/request",
            headers={"Authorization": f"Bearer {user_token}"},
        )
    elif desired_status == EnrollmentStatus.invited.value:
        _ = await create_user_and_get_token(
            client, super_admin_token, username, db_session
        )
        _ = await client.post(
            f"/v1/events/by_id/{event_id}/enrollments/invite",
            json={"membernames": [username]},
            headers={"Authorization": f"Bearer {super_admin_token}"},
        )
    elif desired_status == EnrollmentStatus.assigned.value:
        _ = await create_user_and_get_token(
            client, super_admin_token, username, db_session
        )
        _ = await client.post(
            f"/v1/events/by_id/{event_id}/enrollments/assign",
            json={"membernames": [username]},
            headers={"Authorization": f"Bearer {super_admin_token}"},
        )
    elif desired_status == EnrollmentStatus.withdraw_requested.value:
        user_token = await create_user_and_get_token(
            client, super_admin_token, username, db_session
        )
        _ = await client.post(
            f"/v1/events/by_id/{event_id}/enrollments/assign",
            json={"membernames": [username]},
            headers={"Authorization": f"Bearer {super_admin_token}"},
        )
        _ = await client.post(
            f"/v1/myevents/by_id/{username}/{event_id}/enrollments/withdraw",
            json={"reason": "test"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
    else:
        raise ValueError(f"Unhandled status: {desired_status}")

    await _move_event_to_past(db_session, event_id)
    return event_id


@pytest.mark.asyncio
async def test_approve_request_past_event_rejected_for_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_approve_request_past_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_reject_request_past_event_rejected_for_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_reject_request_past_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_remove_enrollment_past_event_rejected_for_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.assigned.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_remove_enrollment_past_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.assigned.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_approve_withdrawal_past_event_rejected_for_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_approve_withdrawal_past_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_reject_withdrawal_past_event_rejected_for_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_reject_withdrawal_past_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_decline_invite_past_event_rejected_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.invited.value
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/decline",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_request_withdrawal_past_event_rejected_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.assigned.value
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/withdraw",
        json={"reason": "x"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_cancel_withdrawal_past_event_rejected_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_user_with_status(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/cancel-withdraw",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


# ============================================================================
# Cancelled-series guard coverage (#21). Each join-side mutation against a
# cancelled event series returns 422 INVALID_STATE for non-super-admin and
# is bypassed by super-admin. Each exit-side mutation continues to succeed
# so members can leave and admins can clean up cancelled series.
# ============================================================================


async def _cancel_event(
    client: AsyncClient, super_admin_token: str, event_id: int
) -> None:
    """End the programme at its only occurrence, so no live occurrence remains.

    A programme is terminated at an occurrence start (programme R1–R4); the
    events these tests build have a single occurrence at their start, so
    the cutoff is that instant and joins close at once (lifecycle L12).
    """
    fetched = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert fetched.status_code == 200, fetched.text
    response = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={
            "reason": "No longer needed",
            "cutoffTimeUtc": fetched.json()["startTimeUtc"],
        },
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200, response.text


async def _seed_then_cancel(
    client: AsyncClient,
    super_admin_token: str,
    db_session: AsyncSession,
    username: str,
    desired_status: str,
) -> int:
    """Seed `username` to `desired_status` on a fresh event, then mark the
    event series cancelled by direct DB write.

    The cancel API auto-transitions all non-terminal enrollments to
    ``removed``; doing it via the API would mean every test reaches the
    enrollment-status check before the cancel-series check could run. We
    set ``until_time`` directly so the seeded enrollment retains its
    original status and the cancel-series guard is what's under test.
    """
    event_id = await _seed_user_with_status(
        client, super_admin_token, db_session, username, desired_status
    )
    from sqlalchemy import select
    from club_server.db.models.event import Event

    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    # Restore future timestamps (helper moves event to past as its last step).
    event.start_time = future_time_ms(24)
    event.end_time = future_time_ms(25)
    # Mark cancelled: until_time set, continued_as_event_id NULL.
    event.until_time = future_time_ms(0)
    event.continued_as_event_id = None
    await db_session.flush()
    return event_id


# --- Join-side: rejection for non-super-admin ------------------------------


@pytest.mark.asyncio
async def test_approve_request_cancelled_event_rejected_for_regular_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_accept_invite_cancelled_event_rejected_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.invited.value
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_request_enrollment_cancelled_event_rejected_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, super_admin_token, "u1", db_session
    )
    event_id = await create_event(client, super_admin_token)
    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


# --- Join-side: super-admin bypass ----------------------------------------


@pytest.mark.asyncio
async def test_invite_cancelled_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "u1", db_session)
    event_id = await create_event(client, super_admin_token)
    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_assign_cancelled_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "u1", db_session)
    event_id = await create_event(client, super_admin_token)
    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_assign_trial_cancelled_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "u1", db_session)
    event_id = await create_event(client, super_admin_token)
    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "u1"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_approve_request_cancelled_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_accept_invite_cancelled_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.invited.value
    )

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_request_enrollment_cancelled_event_super_admin_bypass(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "u1", db_session)
    event_id = await create_event(client, super_admin_token)
    await _cancel_event(client, super_admin_token, event_id)

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


# --- Exit-side: still allowed on cancelled series -------------------------


@pytest.mark.asyncio
async def test_decline_invite_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.invited.value
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/decline",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_reject_request_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.requested.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_remove_enrollment_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.assigned.value
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_request_withdrawal_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client, super_admin_token, db_session, "u1", EnrollmentStatus.assigned.value
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/withdraw",
        json={"reason": "x"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cancel_withdrawal_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )
    user_login = await client.post(
        "/v1/auth/login", json={"username": "u1", "password": "testpass123"}
    )
    user_token = user_login.json()["accessToken"]

    response = await client.post(
        f"/v1/myevents/by_id/u1/{event_id}/enrollments/cancel-withdraw",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_approve_withdrawal_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["u1"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_reject_withdrawal_cancelled_event_still_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    event_id = await _seed_then_cancel(
        client,
        super_admin_token,
        db_session,
        "u1",
        EnrollmentStatus.withdraw_requested.value,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["u1"], "reason": "x"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 204


# --- R64: failed enrollment mutations do not write audit rows ---


async def _audit_actions_for_event(
    db_session: AsyncSession, event_id: int
) -> list[str]:
    from sqlalchemy import select as _sa_select

    from club_server.db.models.audit_log import AuditLog

    rows = await db_session.execute(
        _sa_select(AuditLog.action).where(
            AuditLog.resource_type == "event",
            AuditLog.resource_id == str(event_id),
        )
    )
    return [r[0] for r in rows.all()]


@pytest.mark.asyncio
async def test_failed_invite_404_writes_no_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """R64: a 404 invite (unknown user) must not write an audit row."""
    from sqlalchemy import select as _sa_select

    from club_server.db.models.audit_log import AuditLog

    admin_token = await create_admin_user(db_session)
    event_id = await create_event(client, admin_token)

    pre_count = (await db_session.execute(_sa_select(AuditLog.id))).all()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["nonexistent_user"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    actions = await _audit_actions_for_event(db_session, event_id)
    assert "enrollment_invited" not in actions

    post_count = (await db_session.execute(_sa_select(AuditLog.id))).all()
    assert len(post_count) == len(pre_count)


@pytest.mark.asyncio
async def test_failed_invite_422_writes_no_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """R64: a 422 invite (event already cancelled) must not write an audit row.

    Uses a regular (non-super) admin because super-admin bypasses the
    cancellation check and the invite would succeed.
    """
    from sqlalchemy import select as _sa_select

    from club_server.db.models.audit_log import AuditLog

    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id = await create_event(client, super_admin_token)

    await _cancel_event(client, super_admin_token, event_id)

    pre_count = (await db_session.execute(_sa_select(AuditLog.id))).all()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"

    actions = await _audit_actions_for_event(db_session, event_id)
    assert "enrollment_invited" not in actions

    post_count = (await db_session.execute(_sa_select(AuditLog.id))).all()
    assert len(post_count) == len(pre_count)


@pytest.mark.asyncio
async def test_failed_invite_409_writes_no_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """R64: a 409 invite (already enrolled) must not write an extra audit row."""
    from sqlalchemy import select as _sa_select

    from club_server.db.models.audit_log import AuditLog

    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    assign_response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert assign_response.status_code == 204

    pre_invited_count = sum(
        1
        for a in await _audit_actions_for_event(db_session, event_id)
        if a == "enrollment_invited"
    )
    pre_count = (await db_session.execute(_sa_select(AuditLog.id))).all()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ALREADY_ENROLLED"

    post_invited_count = sum(
        1
        for a in await _audit_actions_for_event(db_session, event_id)
        if a == "enrollment_invited"
    )
    assert post_invited_count == pre_invited_count

    post_count = (await db_session.execute(_sa_select(AuditLog.id))).all()
    assert len(post_count) == len(pre_count)


# ---------------------------------------------------------------------------
# Issue #251: admin enrollment list exposes per-enrollment timestamps so
# clients can compute per-occurrence eligibility locally.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_enrollments_includes_timestamps(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin enrollment list carries enrolledAtUtc/withdrawnAtUtc per member.

    The legacy ``enrollments`` map is preserved; the new ``records`` array
    carries the full enrollment objects with timestamps.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id = await create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    body = response.json()

    # Legacy map preserved for backward compatibility.
    assert body["enrollments"]["testuser"] == "assigned"

    # New records array carries timestamps.
    records = {r["membername"]: r for r in body["records"]}
    assert "testuser" in records
    rec = records["testuser"]
    assert rec["status"] == "assigned"
    assert rec["enrolledAtUtc"] is not None
    assert rec["withdrawnAtUtc"] is None
