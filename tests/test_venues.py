import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timedelta, timezone

from .helpers import create_admin_user, create_regular_admin_user
from .redesign_helpers import occurrence_version


def future_time(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


@pytest.mark.asyncio
async def test_create_venue(client: AsyncClient, db_session: AsyncSession):
    """Test creating a venue."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/venues",
        json={
            "name": "Main Court",
            "address": "123 Sports Ave",
            "description": "Indoor basketball court",
            "isDefault": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Main Court"
    assert data["address"] == "123 Sports Ave"
    assert data["isDefault"] is True


@pytest.mark.asyncio
async def test_create_venue_with_all_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """Test creating a venue with all fields."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/venues",
        json={
            "name": "Full Venue",
            "address": "456 Main St",
            "description": "A complete venue",
            "mapUri": "https://maps.example.com/venue",
            "isDefault": False,
            "isFeatured": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Full Venue"
    assert data["mapUri"] == "https://maps.example.com/venue"
    assert data["isFeatured"] is True


@pytest.mark.asyncio
async def test_create_duplicate_default_venue_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that creating a second default venue fails."""
    token = await create_admin_user(db_session)

    _ = await client.post(
        "/v1/venues",
        json={"name": "Venue 1", "isDefault": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        "/v1/venues",
        json={"name": "Venue 2", "isDefault": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert "DEFAULT_VENUE_EXISTS" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_list_venues(client: AsyncClient, db_session: AsyncSession):
    """Test listing venues."""
    token = await create_admin_user(db_session)

    _ = await client.post(
        "/v1/venues",
        json={"name": "Venue A"},
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/venues",
        json={"name": "Venue B"},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/venues",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_get_venue(client: AsyncClient, db_session: AsyncSession):
    """Test getting a venue by ID."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Test Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/venues/by_id/{venue_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Test Venue"


@pytest.mark.asyncio
async def test_get_nonexistent_venue_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test getting a nonexistent venue fails."""
    admin_token = await create_admin_user(db_session)
    response = await client.get(
        "/v1/venues/by_id/99999", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert "VENUE_NOT_FOUND" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_update_venue(client: AsyncClient, db_session: AsyncSession):
    """Test updating a venue."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Original Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/venues/by_id/{venue_id}",
        json={"name": "Updated Name", "description": "New description"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["description"] == "New description"


@pytest.mark.asyncio
async def test_update_venue_to_default_clears_previous(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that setting a new default venue clears the previous default."""
    token = await create_admin_user(db_session)

    create1 = await client.post(
        "/v1/venues",
        json={"name": "Venue 1", "isDefault": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue1_id = create1.json()["id"]

    create2 = await client.post(
        "/v1/venues",
        json={"name": "Venue 2"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue2_id = create2.json()["id"]

    _ = await client.patch(
        f"/v1/venues/by_id/{venue2_id}",
        json={"isDefault": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    venue1_response = await client.get(
        f"/v1/venues/by_id/{venue1_id}", headers={"Authorization": f"Bearer {token}"}
    )
    venue2_response = await client.get(
        f"/v1/venues/by_id/{venue2_id}", headers={"Authorization": f"Bearer {token}"}
    )

    assert venue1_response.json()["isDefault"] is False
    assert venue2_response.json()["isDefault"] is True


@pytest.mark.asyncio
async def test_soft_delete_venue(client: AsyncClient, db_session: AsyncSession):
    """Test soft deleting a venue."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "To Delete"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    response = await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["deletedAtUtc"] is not None

    # Verify venue is still accessible via GET but has deletedAtUtc set
    get_response = await client.get(
        f"/v1/venues/by_id/{venue_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_soft_delete_venue_with_active_events_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft deleting a venue with active events is blocked."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Venue with Events"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    # Create a future event at this venue
    _ = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete should fail with active events
    response = await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "VENUE_HAS_EVENTS" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_soft_delete_venue_after_events_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft deleting a venue succeeds after its events are soft-deleted."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Venue with Events"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    # Create a future event at this venue
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    # Soft delete the event first
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Now soft delete the venue — should succeed
    response = await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_restore_venue(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a soft-deleted venue."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "To Restore"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    # Soft delete
    await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Restore
    restore_response = await client.post(
        f"/v1/venues/by_id/{venue_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["id"] == venue_id
    assert restore_response.json()["deletedAtUtc"] is None

    # Verify venue is visible again with no deletedAtUtc
    get_response = await client.get(
        f"/v1/venues/by_id/{venue_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is None


@pytest.mark.asyncio
async def test_restore_venue_not_deleted(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a venue that is not deleted fails."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Active Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    restore_response = await client.post(
        f"/v1/venues/by_id/{venue_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 422


@pytest.mark.asyncio
async def test_hard_delete_venue(client: AsyncClient, db_session: AsyncSession):
    """Test hard deleting a venue (super admin)."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "To Wipeout"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    # Soft delete first (required before hard delete)
    await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.delete(
        f"/v1/venues/by_id/{venue_id}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Verify venue is permanently gone
    restore_response = await client.post(
        f"/v1/venues/by_id/{venue_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 404


@pytest.mark.asyncio
async def test_hard_delete_venue_requires_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard DELETE requires super admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Protected Venue"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    venue_id = create_response.json()["id"]

    # Regular admin should fail on hard delete
    delete_response = await client.delete(
        f"/v1/venues/by_id/{venue_id}/hard",
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert delete_response.status_code == 403


@pytest.mark.asyncio
async def test_hard_delete_venue_with_events_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard deleting a venue with events fails even after soft delete."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "Venue with Events"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    # Create an event at this venue
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    # Soft delete the event first (venue soft-delete requires no active events)
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete the venue (succeeds because event is soft-deleted)
    await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Hard delete should still fail (soft-deleted events still reference this venue)
    response = await client.delete(
        f"/v1/venues/by_id/{venue_id}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "VENUE_HAS_EVENTS" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_soft_deleted_venue_excluded_from_list(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft-deleted venues don't appear in the active list."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/venues",
        json={"name": "To Soft Delete"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = create_response.json()["id"]

    # Soft delete via DELETE
    await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Venue should no longer appear in active list
    list_response = await client.get(
        "/v1/venues",
        headers={"Authorization": f"Bearer {token}"},
    )
    venues = [v for v in list_response.json()["items"] if v["id"] == venue_id]
    assert len(venues) == 0

    # Venue should appear in deleted list with deletedAtUtc set
    deleted_response = await client.get(
        "/v1/venues/deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    deleted_venues = [
        v for v in deleted_response.json()["items"] if v["id"] == venue_id
    ]
    assert len(deleted_venues) == 1
    assert deleted_venues[0]["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_soft_delete_venue_blocked_by_occurrence_override(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft deleting a venue is blocked when an occurrence override references it."""
    token = await create_admin_user(db_session)

    venue1_response = await client.post(
        "/v1/venues",
        json={"name": "Original Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue1_id = venue1_response.json()["id"]

    venue2_response = await client.post(
        "/v1/venues",
        json={"name": "Override Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue2_id = venue2_response.json()["id"]

    # Create a one-day camp at venue1
    # A camp's slots are whole seconds; a one-off's occurrence is not
    # rescheduled on its own (#472).
    start_ms = future_time(24) // 1000 * 1000
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "camp",
            "rrule": "FREQ=DAILY;COUNT=1",
            "venueId": venue1_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": start_ms + 60 * 60 * 1000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    # Reschedule occurrence to venue2
    await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "newVenueId": venue2_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete venue2 should fail — occurrence override references it
    response = await client.delete(
        f"/v1/venues/by_id/{venue2_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "VENUE_HAS_EVENTS" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_hard_delete_venue_blocked_by_occurrence_override(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard deleting a venue is blocked when an occurrence override references it."""
    token = await create_admin_user(db_session)

    venue1_response = await client.post(
        "/v1/venues",
        json={"name": "Original Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue1_id = venue1_response.json()["id"]

    venue2_response = await client.post(
        "/v1/venues",
        json={"name": "Override Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue2_id = venue2_response.json()["id"]

    # Create a one-day camp at venue1
    # A camp's slots are whole seconds; a one-off's occurrence is not
    # rescheduled on its own (#472).
    start_ms = future_time(24) // 1000 * 1000
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "camp",
            "rrule": "FREQ=DAILY;COUNT=1",
            "venueId": venue1_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": start_ms + 60 * 60 * 1000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    # Reschedule occurrence to venue2
    await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_ms}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, start_ms),
            "newVenueId": venue2_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete the event so venue2 can be soft-deleted
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete venue2 succeeds (event is soft-deleted)
    await client.delete(
        f"/v1/venues/by_id/{venue2_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Hard delete venue2 should fail — soft-deleted event's override still references it
    response = await client.delete(
        f"/v1/venues/by_id/{venue2_id}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "VENUE_HAS_EVENTS" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_venue_requires_auth_for_create(client: AsyncClient):
    """Test that venue creation requires authentication."""
    response = await client.post(
        "/v1/venues",
        json={"name": "Test Venue"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_venue_requires_admin_for_create(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that venue creation requires admin role."""
    admin_token = await create_admin_user(db_session)

    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": "regular_user",
            "email": "regular@example.com",
            "password": "testpass123",
            "firstName": "Regular",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    _ = await client.post(
        "/v1/users/by_id/regular_user/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "regular_user", "password": "testpass123"},
    )
    user_token = login_response.json()["accessToken"]

    response = await client.post(
        "/v1/venues",
        json={"name": "Test Venue"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403
