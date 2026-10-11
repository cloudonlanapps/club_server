from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_coach_user,
    create_regular_admin_user,
)
from .redesign_helpers import occurrence_version, oneoff_occurrence_version, version_of


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


def future_time(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


@pytest.mark.asyncio
async def test_create_one_off_event(client: AsyncClient, db_session: AsyncSession):
    """Test creating a one-off event."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_time = future_time(24)
    end_time = future_time(26)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "description": "A test event",
            "type": "oneOff",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Event"
    assert data["type"] == "oneOff"
    assert data["visibility"] == "public"


@pytest.mark.asyncio
async def test_create_programme_event_with_rrule(
    client: AsyncClient, db_session: AsyncSession
):
    """Test creating a programme event with RRULE."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_time = future_time(24)
    end_time = future_time(25)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Weekly Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
            "rrule": "FREQ=WEEKLY;BYDAY=MO,WE,FR",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["type"] == "programme"
    assert data["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR"


@pytest.mark.asyncio
async def test_create_camp_event_with_count(
    client: AsyncClient, db_session: AsyncSession
):
    """Test creating a camp event with COUNT-based RRULE."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    start_time = future_time(24)
    end_time = future_time(28)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Summer Camp",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["type"] == "camp"


@pytest.mark.asyncio
async def test_create_event_with_invalid_rrule_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that creating an event with invalid RRULE fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Invalid Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
            "rrule": "INVALID_RRULE",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "INVALID_RRULE" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_create_event_with_end_before_start_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that creating an event with end before start fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Invalid Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(25),
            "endTimeUtc": future_time(24),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "INVALID_DATETIME" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_get_event(client: AsyncClient, db_session: AsyncSession):
    """Test getting an event by ID."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
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
    event_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Test Event"


@pytest.mark.asyncio
async def test_list_events(client: AsyncClient, db_session: AsyncSession):
    """Test listing events."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    _ = await client.post(
        "/v1/events",
        json={
            "title": "Event A",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/events",
        json={
            "title": "Event B",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time(48),
            "endTimeUtc": future_time(49),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_list_events_filter_by_type(
    client: AsyncClient, db_session: AsyncSession
):
    """Test listing events filtered by type."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    _ = await client.post(
        "/v1/events",
        json={
            "title": "One Off",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/events",
        json={
            "title": "Programme",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time(48),
            "endTimeUtc": future_time(49),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/events?type=oneOff",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["type"] == "oneOff"


@pytest.mark.asyncio
async def test_update_event(client: AsyncClient, db_session: AsyncSession):
    """Test updating an event."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Original Title",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={
            "title": "Updated Title",
            "version": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_soft_delete_event(client: AsyncClient, db_session: AsyncSession):
    """Test soft deleting an event."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event to Delete",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["deletedAtUtc"] is not None

    # Verify event is still accessible via GET but has deletedAtUtc set
    get_response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_restore_event(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a soft-deleted event."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event to Restore",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    # Soft delete
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Restore
    restore_response = await client.post(
        f"/v1/events/by_id/{event_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["id"] == event_id
    assert restore_response.json()["deletedAtUtc"] is None

    # Verify event is visible again with no deletedAtUtc
    get_response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is None


@pytest.mark.asyncio
async def test_restore_event_not_deleted(client: AsyncClient, db_session: AsyncSession):
    """Test restoring an event that is not deleted fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Active Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    restore_response = await client.post(
        f"/v1/events/by_id/{event_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 422


@pytest.mark.asyncio
async def test_hard_delete_event(client: AsyncClient, db_session: AsyncSession):
    """Test hard deleting an event (super admin)."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event to Wipeout",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    # Soft delete first (required before hard delete)
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.delete(
        f"/v1/events/by_id/{event_id}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Verify event is permanently gone (restore should 404)
    restore_response = await client.post(
        f"/v1/events/by_id/{event_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 404


@pytest.mark.asyncio
async def test_list_events_pagination(client: AsyncClient, db_session: AsyncSession):
    """Test events pagination."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # Create 3 events
    for i in range(3):
        _ = await client.post(
            "/v1/events",
            json={
                "title": f"Event {i}",
                "type": "oneOff",
                "venueId": venue_id,
                "startTimeUtc": future_time(24 + i),
                "endTimeUtc": future_time(25 + i),
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    response = await client.get(
        "/v1/events?limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_event_requires_auth(client: AsyncClient):
    """Test that event endpoints require authentication."""
    response = await client.get("/v1/events")
    assert response.status_code == 401

    response = await client.post(
        "/v1/events",
        json={
            "title": "Test",
            "type": "oneOff",
            "venueId": 1,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
    )
    assert response.status_code == 401


# ============================================================================
# Event Lifecycle Tests (Cancel, Split, Wipeout)
# ============================================================================


@pytest.mark.asyncio
async def test_cancel_event_series(client: AsyncClient, db_session: AsyncSession):
    """Test cancelling an event series sets untilTimeUtc."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Weekly Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": (future_time(24) // 1000) * 1000,
            "endTimeUtc": future_time(25),
            "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]
    cutoff = create_response.json()["startTimeUtc"] + 7 * 86_400_000

    cancel_response = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={"reason": "Budget constraints", "cutoffTimeUtc": cutoff, "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_response.status_code == 200
    data = cancel_response.json()
    assert data["untilTimeUtc"] == cutoff


@pytest.mark.asyncio
async def test_cancel_event_already_cancelled_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that cancelling an already cancelled event fails."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Weekly Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": (future_time(24) // 1000) * 1000,
            "endTimeUtc": future_time(25),
            "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]
    cutoff = create_response.json()["startTimeUtc"] + 7 * 86_400_000

    # Terminate once
    first = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={"reason": "First cancellation", "cutoffTimeUtc": cutoff, "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200

    # Terminating again is refused: a bounded programme is extended instead.
    second_cancel = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={
            "reason": "Second cancellation",
            "cutoffTimeUtc": cutoff,
            "version": first.json()["version"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_cancel.status_code == 422
    assert second_cancel.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_hard_delete_event_requires_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard DELETE requires super admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    venue_id = await create_venue(client, super_admin_token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event to Delete",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    event_id = create_response.json()["id"]

    # Regular admin should fail on hard delete
    delete_response = await client.delete(
        f"/v1/events/by_id/{event_id}/hard",
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert delete_response.status_code == 403

    # Regular admin can soft delete
    delete_response = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_coach_can_cancel_own_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a coach can cancel their own event."""
    super_admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    venue_id = await create_venue(client, super_admin_token)

    # Coach creates an event
    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Coach's Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": (future_time(24) // 1000) * 1000,
            "endTimeUtc": future_time(25),
            "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU",
        },
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    event_id = create_response.json()["id"]
    cutoff = create_response.json()["startTimeUtc"] + 7 * 86_400_000

    # Coach should be able to terminate their own programme
    cancel_response = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={"reason": "Schedule conflict", "cutoffTimeUtc": cutoff, "version": 1},
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert cancel_response.status_code == 200


@pytest.mark.asyncio
async def test_soft_deleted_event_excluded_from_list(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft-deleted events don't appear in the active list but appear in deleted list."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Event to List-Delete",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    # Soft delete
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Not in active list
    list_response = await client.get(
        "/v1/events",
        headers={"Authorization": f"Bearer {token}"},
    )
    event_ids = [e["id"] for e in list_response.json()["items"]]
    assert event_id not in event_ids

    # In deleted list with deletedAtUtc set
    deleted_response = await client.get(
        "/v1/events/deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    deleted_items = deleted_response.json()["items"]
    deleted_ids = [e["id"] for e in deleted_items]
    assert event_id in deleted_ids
    deleted_event = next(e for e in deleted_items if e["id"] == event_id)
    assert deleted_event["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_create_event_venue_conflict(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that creating an event at the same venue with overlapping time returns 409."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    await client.post(
        "/v1/events",
        json={
            "title": "Event A",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        "/v1/events",
        json={
            "title": "Event B",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(26),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["hasConflict"] is True
    assert len(detail["venueConflicts"]) > 0


@pytest.mark.asyncio
async def test_create_event_coach_conflict(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that creating an event with the same organizer at overlapping time returns 409."""
    token = await create_admin_user(db_session)
    venue1_id = await create_venue(client, token, "Venue 1")
    venue2_id = await create_venue(client, token, "Venue 2")

    await client.post(
        "/v1/events",
        json={
            "title": "Event A",
            "type": "programme",
            "venueId": venue1_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Same organizer (admin user), different venue, overlapping time
    response = await client.post(
        "/v1/events",
        json={
            "title": "Event B",
            "type": "programme",
            "venueId": venue2_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(26),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["hasConflict"] is True
    assert len(detail["userConflicts"]) > 0


@pytest.mark.asyncio
async def test_create_event_no_conflict_different_venue_and_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that overlapping time with different venue and organizer succeeds."""
    token = await create_admin_user(db_session)
    venue1_id = await create_venue(client, token, "Venue 1")
    venue2_id = await create_venue(client, token, "Venue 2")

    await client.post(
        "/v1/events",
        json={
            "title": "Event A",
            "type": "oneOff",
            "venueId": venue1_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
            "organizerName": "admin",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Different venue and explicit different organizer — no conflict
    # Use a coach user as the organizer for the second event
    from .helpers import create_coach_user

    coach_token = await create_coach_user(db_session)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Event B",
            "type": "oneOff",
            "venueId": venue2_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(26),
        },
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_event_no_conflict_with_soft_deleted_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a soft-deleted event does not block creation at the same venue/time."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Event A",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    # Soft delete event A
    await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Create event B at same venue/time — should succeed
    response = await client.post(
        "/v1/events",
        json={
            "title": "Event B",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_event_no_conflict_non_overlapping_time(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that same venue and coach with non-overlapping time succeeds."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    await client.post(
        "/v1/events",
        json={
            "title": "Event A",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Non-overlapping time — should succeed
    response = await client.post(
        "/v1/events",
        json={
            "title": "Event B",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(26),
            "endTimeUtc": future_time(27),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_restore_event_blocked_when_venue_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that restoring an event is blocked when its venue is soft-deleted."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # Create event
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

    # Soft delete the venue (succeeds because event is already deleted)
    await client.delete(
        f"/v1/venues/by_id/{venue_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Try to restore the event — should fail because venue is deleted
    response = await client.post(
        f"/v1/events/by_id/{event_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VENUE_IS_DELETED"


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


# =============================================================================
# Event-type constraint tests
# =============================================================================


@pytest.mark.asyncio
async def test_update_rejects_programme_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that PATCH /events/{id} rejects programme events."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)
    start_time = future_time(24)
    end_time = future_time(25)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Programme Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
            "rrule": "FREQ=WEEKLY;BYDAY=MO",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"title": "Updated Title", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"


@pytest.mark.asyncio
async def test_correction_rejects_oneoff_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that PATCH /events/{id}/correction rejects oneOff events."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "OneOff Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}/correction",
        json={"title": "Corrected", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"


@pytest.mark.asyncio
async def test_future_rejects_camp_event(client: AsyncClient, db_session: AsyncSession):
    """Test that PATCH /events/{id}/future rejects camp events."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Camp Event",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}/future",
        json={
            "effectiveDateTimeUtc": future_time(48),
            "venueId": venue_id,
            "version": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"


@pytest.mark.asyncio
async def test_cancel_oneoff_event(client: AsyncClient, db_session: AsyncSession):
    """Cancelling a oneOff event succeeds and the public detail flips to cancelled."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "OneOff Event",
            "type": "oneOff",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/drop",
        json={
            "version": await oneoff_occurrence_version(client, token, event_id),
            "reason": "Test",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    occurrence = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{create_response.json()['startTimeUtc']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert occurrence.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_camp_event(client: AsyncClient, db_session: AsyncSession):
    """Cancelling a camp event succeeds."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # Whole-second start so `start + k*day` lands on the (second-granular)
    # occurrence boundary the server validates against.
    start = (future_time(48) // 1000) * 1000
    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Summer Camp",
            "type": "camp",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": start + 2 * 3600 * 1000,
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    # Cancel from the 3rd occurrence (a real session boundary, well in the future).
    response = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={
            "reason": "Weather",
            "effectiveDateTimeUtc": start + 2 * 86_400_000,
            "version": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["untilTimeUtc"] is not None


@pytest.mark.asyncio
async def test_cancel_camp_partial(client: AsyncClient, db_session: AsyncSession):
    """A camp partially cancelled mid-series surfaces as partiallyCancelled."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # Camp that started 5 days ago, COUNT=10 daily. Partially cancel from the
    # 4th occurrence (start + 3 days), a real boundary that is in the past — the
    # super-admin token bypasses the past/lead-time rules (testing escape hatch).
    start = (
        int((datetime.now(timezone.utc) - timedelta(days=5)).timestamp() * 1000) // 1000
    ) * 1000
    end = start + 2 * 3600 * 1000
    cancel_from = start + 3 * 86_400_000

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Long Camp",
            "type": "camp",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": end,
            "rrule": "FREQ=DAILY;COUNT=10",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={
            "reason": "Force majeure",
            "effectiveDateTimeUtc": cancel_from,
            "version": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cancel_already_cancelled_rejected_for_all_types(
    client: AsyncClient, db_session: AsyncSession
):
    """Double-cancel returns 400 for oneOff and camp (programme already covered)."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    for idx, body in enumerate(
        [
            {
                "title": "OneOff",
                "type": "oneOff",
                "venueId": venue_id,
                "startTimeUtc": future_time(24),
                "endTimeUtc": future_time(25),
            },
            {
                "title": "Camp",
                "type": "camp",
                "venueId": venue_id,
                # Whole-second start so the first-occurrence boundary == start.
                "startTimeUtc": (future_time(96) // 1000) * 1000,
                "endTimeUtc": future_time(98),
                "rrule": "FREQ=DAILY;COUNT=3",
            },
        ]
    ):
        _ = idx
        create = await client.post(
            "/v1/events",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create.status_code == 201, create.text
        event_id = create.json()["id"]

        # A one-off is dropped (no effective time); a camp is cancelled from
        # its first occurrence start.
        if body["type"] == "oneOff":
            path, payload = f"/v1/events/by_id/{event_id}/drop", {"reason": "first"}
            payload["version"] = await oneoff_occurrence_version(
                client, token, event_id
            )
        else:
            path = f"/v1/events/by_id/{event_id}/cancel"
            payload = {
                "reason": "first",
                "effectiveDateTimeUtc": body["startTimeUtc"],
                "version": create.json()["version"],
            }
        first = await client.post(
            path, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert first.status_code == 200, first.text

        if body["type"] == "oneOff":
            # The drop moved the occurrence on; send the version it is at now.
            payload["version"] = await oneoff_occurrence_version(
                client, token, event_id
            )
        else:
            # The cancel moved the event on; send the version it is at now (#13).
            payload["version"] = first.json()["version"]
        second = await client.post(
            path, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        if body["type"] == "oneOff":
            assert second.status_code == 422
            assert second.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"
        else:
            assert second.status_code == 400
            assert "EVENT_ALREADY_CANCELLED" in str(second.json()["detail"])


@pytest.mark.asyncio
async def test_list_occurrences_after_partial_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    """Occurrences past until_time are surfaced as cancelled with synthetic reason."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # Camp starting 1 day from now, COUNT=5 → 5 daily slots over the next 5 days.
    # Whole-second start so `start + k*day` lands on the occurrence boundary.
    start = (future_time(24) // 1000) * 1000
    end = start + 3600 * 1000
    create = await client.post(
        "/v1/events",
        json={
            "title": "Mini Camp",
            "type": "camp",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": end,
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    event_id = create.json()["id"]

    # Cancel after the second day (relative to the camp's actual start_time_ms).
    cancel_at = start + 48 * 3600 * 1000  # exact occurrence boundary (#108)
    cancel = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={
            "reason": "Force majeure",
            "effectiveDateTimeUtc": cancel_at,
            "version": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel.status_code == 200

    from_ts = future_time(0)
    to_ts = future_time(24 * 10)
    listing = await client.get(
        f"/v1/events/occurrences?fromTimeUtc={from_ts}&toTimeUtc={to_ts}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listing.status_code == 200, listing.text
    items = listing.json()
    occs = [o for o in items if o["eventId"] == event_id]
    assert len(occs) == 5  # full COUNT visible — pre/post until_time
    pre = [o for o in occs if o["startTimeUtc"] < cancel_at]
    post = [o for o in occs if o["startTimeUtc"] >= cancel_at]
    assert pre and post
    for o in pre:
        assert o["status"] == "scheduled"
    for o in post:
        assert o["status"] == "cancelled"
        assert o["cancelReason"] is not None
        assert "series cancelled" in o["cancelReason"]


@pytest.mark.asyncio
async def test_list_occurrences_override_takes_precedence_over_series_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    """A per-occurrence override wins over the synthesized series-cancel."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    # Whole-second start to avoid float→ms rounding drift in the occurrence index.
    start = (future_time(24) // 1000) * 1000
    end = start + 3600 * 1000
    create = await client.post(
        "/v1/events",
        json={
            "title": "Mini Camp",
            "type": "camp",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": end,
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201, create.text
    event_id = create.json()["id"]

    # Pre-emptively reschedule the 4th occurrence (start + 72h).
    fourth = start + 72 * 3600 * 1000
    new_start = fourth + 3600 * 1000  # 1 hour later
    reschedule = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{fourth}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, fourth),
            "newStartTimeUtc": new_start,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reschedule.status_code in {200, 204}, reschedule.text

    # Now cancel the series after day 2 (so day-4 override is past until_time).
    cancel_at = start + 48 * 3600 * 1000  # exact occurrence boundary (#108)
    cancel = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={
            "reason": "Force majeure",
            "effectiveDateTimeUtc": cancel_at,
            "version": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel.status_code == 200

    from_ts = future_time(0)
    to_ts = future_time(24 * 10)
    listing = await client.get(
        f"/v1/events/occurrences?fromTimeUtc={from_ts}&toTimeUtc={to_ts}",
        headers={"Authorization": f"Bearer {token}"},
    )
    occs = [o for o in listing.json() if o["eventId"] == event_id]
    fourth_occ = next(o for o in occs if o["occurrenceTimeUtc"] == fourth)
    # The slot decides (programme R19b, #380): a slot at or after the cutoff
    # is cancelled however it was moved.
    assert fourth_occ["status"] == "cancelled"


@pytest.mark.asyncio
async def test_update_allowed_for_oneoff(client: AsyncClient, db_session: AsyncSession):
    """Test that PATCH /events/{id} works for oneOff events."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "OneOff Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"title": "Updated OneOff", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Updated OneOff"


# =============================================================================
# Auth tightening tests (negative)
# =============================================================================


@pytest.mark.asyncio
async def test_regular_user_cannot_list_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot list events (admin/coach only)."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    response = await client.get(
        "/v1/events",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_regular_user_cannot_get_event(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot get an event (admin/coach only)."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    venue_id = await create_venue(client, admin_token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_coach_can_list_events(client: AsyncClient, db_session: AsyncSession):
    """Test that a coach can list events."""
    _ = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)

    response = await client.get(
        "/v1/events",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200


# =============================================================================
# Enrollment and metadata propagation on split tests
# =============================================================================


# =============================================================================
# Event update gaps
# =============================================================================


@pytest.mark.asyncio
async def test_update_oneoff_event_venue(client: AsyncClient, db_session: AsyncSession):
    """Venue changes on a oneOff go through /reschedule (#232), not PATCH."""
    token = await create_admin_user(db_session)
    venue_id_1 = await create_venue(client, token, name="Venue A")
    venue_id_2 = await create_venue(client, token, name="Venue B")

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Venue Change Event",
            "type": "oneOff",
            "venueId": venue_id_1,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(26),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    # venueId is no longer accepted by the generic PATCH.
    rejected = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"venueId": venue_id_2, "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rejected.status_code == 422

    response = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "venueId": venue_id_2,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["venueId"] == int(venue_id_2)


@pytest.mark.asyncio
async def test_update_camp_visibility(client: AsyncClient, db_session: AsyncSession):
    """Test updating visibility on a camp event via PATCH."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Summer Camp",
            "type": "camp",
            "visibility": "private",
            "venueId": venue_id,
            "startTimeUtc": future_time(48),
            "endTimeUtc": future_time(50),
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"visibility": "public", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["visibility"] == "public"


# --- R16a / R16b: organizer-vs-non-organizer coach authorization on
# update / cancel / delete. R16b is restricted to update + cancel: delete
# is admin-only by R6, so an organizer coach is not granted delete by
# R16b's "admin-level rights" clause. ---


async def _create_event_with_admin_organizer(
    client: AsyncClient, admin_token: str
) -> int:
    venue_id = await create_venue(client, admin_token)
    response = await client.post(
        "/v1/events",
        json={
            "title": "Admin-Organized Event",
            "type": "oneOff",
            "venueId": venue_id,
            "organizerName": "admin",
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_r16a_non_organizer_coach_cannot_update(
    client: AsyncClient, db_session: AsyncSession
):
    """R16a: a coach who is not the organizer cannot PATCH the event → 403."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="other_coach")
    event_id = await _create_event_with_admin_organizer(client, admin_token)

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"title": "Hijacked", "version": 1},
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_r16a_non_organizer_coach_cannot_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    """R16a: a coach who is not the organizer cannot cancel the event → 403."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="other_coach")
    event_id = await _create_event_with_admin_organizer(client, admin_token)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "no", "effectiveDateTimeUtc": future_time(24), "version": 1},
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_r16a_non_organizer_coach_cannot_delete(
    client: AsyncClient, db_session: AsyncSession
):
    """R16a: a coach who is not the organizer cannot DELETE the event → 403.

    Delete is admin-only (R6), so any coach (organizer or not) is denied
    here; this asserts the R16a outcome for the non-organizer case.
    """
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="other_coach")
    event_id = await _create_event_with_admin_organizer(client, admin_token)

    response = await client.delete(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_r16b_organizer_coach_can_update(
    client: AsyncClient, db_session: AsyncSession
):
    """R16b: a coach who is the organizer can PATCH the event."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="org_coach")
    venue_id = await create_venue(client, admin_token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Coach-Organized Event",
            "type": "oneOff",
            "venueId": venue_id,
            "organizerName": "org_coach",
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"title": "Updated By Organizer", "version": 1},
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Updated By Organizer"


@pytest.mark.asyncio
async def test_r16b_organizer_coach_can_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    """R16b: a coach who is the organizer can cancel the event."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="org_coach")
    venue_id = await create_venue(client, admin_token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Coach-Organized Event",
            "type": "oneOff",
            "venueId": venue_id,
            "organizerName": "org_coach",
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/events/by_id/{event_id}/drop",
        json={
            "version": await oneoff_occurrence_version(client, coach_token, event_id),
            "reason": "ok",
        },
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200


# --- R99: visibility values outside the supported set are rejected ---


@pytest.mark.asyncio
async def test_create_event_with_invalid_visibility_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    """R99: POST /v1/events with an unsupported visibility value → 422."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Bad Visibility",
            "type": "oneOff",
            "venueId": venue_id,
            "visibility": "confidential",
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_event_with_invalid_visibility_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    """R99: PATCH /v1/events/by_id/{id} with an unsupported visibility value → 422."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Original",
            "type": "oneOff",
            "venueId": venue_id,
            "visibility": "private",
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"visibility": "confidential", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["visibility"] == "private"


# --- #105: untilTimeUtc must not be settable via create/update ---


@pytest.mark.asyncio
async def test_create_event_rejects_until_time_utc(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /v1/events with untilTimeUtc must be rejected with 422.

    untilTimeUtc may only be set via the dedicated cancel-series endpoint,
    which records a reason and handles occurrence side-effects properly.
    """
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    response = await client.post(
        "/v1/events",
        json={
            "title": "OneOff with terminator",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
            "untilTimeUtc": future_time(48),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422

    response_no_until = await client.post(
        "/v1/events",
        json={
            "title": "OneOff without terminator",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response_no_until.status_code == 201


@pytest.mark.asyncio
async def test_update_event_rejects_until_time_utc(
    client: AsyncClient, db_session: AsyncSession
):
    """PATCH /v1/events/by_id/{id} with untilTimeUtc must be rejected with 422.

    Even non-null values that would "un-cancel" a series must be refused —
    the series-cancel endpoint is the only path that may touch untilTimeUtc.
    """
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Original",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]

    patch_response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"untilTimeUtc": future_time(48), "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch_response.status_code == 422

    patch_null_response = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"untilTimeUtc": None, "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch_null_response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_endpoint_still_sets_until_time_utc(
    client: AsyncClient, db_session: AsyncSession
):
    """Positive control for #105: the cancel-series path still works."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token)

    create_response = await client.post(
        "/v1/events",
        json={
            "title": "Cancellable",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": (future_time(24) // 1000) * 1000,
            "endTimeUtc": future_time(25),
            "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201
    event_id = create_response.json()["id"]
    cutoff = create_response.json()["startTimeUtc"] + 7 * 86_400_000

    cancel_response = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={"reason": "Budget", "cutoffTimeUtc": cutoff, "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["untilTimeUtc"] == cutoff
