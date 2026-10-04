"""
Phase 8 - Hardening Tests

Edge case coverage for:
- Blocked user exclusion
- Authentication edge cases
- Data integrity
"""

import pytest
from httpx import AsyncClient
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user


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


# ============================================================================
# Blocked User Tests
# ============================================================================


@pytest.mark.requirement("auth:R4")
@pytest.mark.asyncio
async def test_blocked_user_cannot_login(client: AsyncClient, db_session: AsyncSession):
    """Test that blocked users cannot login."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    # Block the user
    _ = await client.post(
        "/v1/users/by_id/testuser/block",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Try to login
    response = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    assert response.status_code == 401
    assert "ACCOUNT_BLOCKED" in str(response.json())


@pytest.mark.requirement("auth:R12")
@pytest.mark.asyncio
async def test_blocked_user_token_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that existing tokens for blocked users are rejected."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    # Verify token works before blocking
    response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200

    # Block the user
    _ = await client.post(
        "/v1/users/by_id/testuser/block",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Token should be rejected
    response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 401


@pytest.mark.requirement("auth:R12")
@pytest.mark.asyncio
async def test_unblock_user_restores_access(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that unblocking a user restores their access."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    # Block the user
    _ = await client.post(
        "/v1/users/by_id/testuser/block",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Unblock the user
    _ = await client.post(
        "/v1/users/by_id/testuser/unblock",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Should be able to login again
    response = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    assert response.status_code == 200


@pytest.mark.requirement("auth:R12")
@pytest.mark.asyncio
async def test_block_unblock_cycle(client: AsyncClient, db_session: AsyncSession):
    """Test that users can be blocked and unblocked multiple times."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    for _ in range(3):
        # Block
        block_response = await client.post(
            "/v1/users/by_id/testuser/block",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert block_response.status_code == 200

        # Verify blocked
        login_response = await client.post(
            "/v1/auth/login",
            json={"username": "testuser", "password": "testpass123"},
        )
        assert login_response.status_code == 401

        # Unblock
        unblock_response = await client.post(
            "/v1/users/by_id/testuser/unblock",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert unblock_response.status_code == 200

        # Verify unblocked
        login_response = await client.post(
            "/v1/auth/login",
            json={"username": "testuser", "password": "testpass123"},
        )
        assert login_response.status_code == 200


# ============================================================================
# Pending User Tests
# ============================================================================


@pytest.mark.requirement("auth:R3")
@pytest.mark.asyncio
async def test_pending_user_login_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    """Pending (unapproved) users can still authenticate (#120).

    Lifecycle reads such as ``GET /v1/users/me`` and ``GET /v1/notifications``
    are open to pending users so the UI can render the "awaiting review"
    state. Only ``blocked`` and ``left`` accounts are turned away at login."""
    _ = await create_admin_user(db_session)

    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": "pending",
            "email": "pending@example.com",
            "password": "testpass123",
            "firstName": "Pending",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    pre_login = await client.post(
        "/v1/auth/login",
        json={"username": "pending", "password": "testpass123"},
    )
    pre_token = pre_login.json()["accessToken"]
    await attach_identity_document(db_session, "pending")
    _ = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {pre_token}"},
    )

    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "pending", "password": "testpass123"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["accessToken"]


# ============================================================================
# Authentication Edge Cases
# ============================================================================


@pytest.mark.requirement("auth:R8")
@pytest.mark.asyncio
async def test_invalid_token_rejected(client: AsyncClient):
    """Test that invalid tokens are rejected."""
    response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": "Bearer invalid_token"},
    )
    assert response.status_code == 401


@pytest.mark.requirement("auth:R8")
@pytest.mark.asyncio
async def test_malformed_auth_header_rejected(client: AsyncClient):
    """Test that malformed auth headers are rejected."""
    response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": "invalid_format"},
    )
    assert response.status_code in [401, 403]


@pytest.mark.requirement("auth:R8")
@pytest.mark.asyncio
async def test_missing_auth_header_rejected(client: AsyncClient):
    """Test that missing auth headers are rejected on protected endpoints."""
    response = await client.get("/v1/auth/me")
    assert response.status_code in [401, 403]


@pytest.mark.requirement("auth:R2")
@pytest.mark.asyncio
async def test_wrong_password_rejected(client: AsyncClient, db_session: AsyncSession):
    """Test that wrong password is rejected."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    response = await client.post(
        "/v1/auth/login",
        json={"username": "testuser", "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.requirement("auth:R2")
@pytest.mark.asyncio
async def test_nonexistent_user_login_rejected(client: AsyncClient):
    """Test that login for non-existent user is rejected."""
    response = await client.post(
        "/v1/auth/login",
        json={"username": "nonexistent", "password": "testpass123"},
    )
    assert response.status_code == 401


# ============================================================================
# Role Assignment Tests
# ============================================================================


@pytest.mark.requirement("users:R32a")
@pytest.mark.asyncio
async def test_non_admin_cannot_assign_roles(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that non-admin users cannot assign roles."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "targetuser", db_session)

    # Regular user tries to assign role
    response = await client.post(
        "/v1/users/by_id/targetuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.requirement("users:R31")
@pytest.mark.asyncio
async def test_cannot_assign_invalid_role(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that invalid roles cannot be assigned."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    response = await client.post(
        "/v1/users/by_id/testuser/roles",
        json={"role": "invalid_role"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.requirement("users:R31")
@pytest.mark.asyncio
async def test_cannot_assign_duplicate_role(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that duplicate roles cannot be assigned."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    # Assign coach role
    _ = await client.post(
        "/v1/users/by_id/testuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Try to assign again
    response = await client.post(
        "/v1/users/by_id/testuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409


# ============================================================================
# Event Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_event_organizer_access(client: AsyncClient, db_session: AsyncSession):
    """Test that event organizers have access to their events."""
    admin_token = await create_admin_user(db_session)
    venue_id = await create_venue(client, admin_token)

    # Create an event
    response = await client.post(
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
    event_id = response.json()["id"]

    # Event should be accessible
    response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Test Event"
