from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
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


def future_time(hours: int = 24) -> datetime:
    """Helper to get a future time."""
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def future_time_ms(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int(future_time(hours).timestamp() * 1000)


def past_time(hours: int = 24) -> datetime:
    """Helper to get a past time."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def past_time_ms(hours: int = 24) -> int:
    """Helper to get a past time as milliseconds since epoch."""
    return int(past_time(hours).timestamp() * 1000)


async def create_venue(client: AsyncClient, token: str) -> int:
    """Helper to create a venue and return its ID."""
    response = await client.post(
        "/v1/venues",
        json={"name": "Test Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


async def create_event_and_enroll(
    client: AsyncClient,
    admin_token: str,
    username: str,
    in_past: bool = False,
    db_session: AsyncSession | None = None,
) -> tuple[int, int]:
    """Helper to create an event, enroll a user, and return event_id and occurrence_time_utc.

    When ``in_past=True`` the event is moved to one hour in the past after
    enrollment via direct DB update. This is required for attendance-marking
    tests: the open window (start − 30 min) must already be open, which is
    no longer bypassed by super-admin (see issue #106).
    """
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

    if not in_past:
        return event_id, start_time

    assert db_session is not None, "db_session is required when in_past=True"
    past_start = await move_event_to_recent_past(db_session, event_id, [username])
    return event_id, past_start


async def move_event_to_recent_past(
    db_session: AsyncSession,
    event_id: int,
    usernames: list[str],
    hours_ago: int = 1,
) -> int:
    """Move ``event_id`` to ``hours_ago`` in the past and backfill enrolled_at.

    Returns the new past start time (ms). Used by attendance tests that need
    the open window (start − 30 min) already satisfied, since super-admin no
    longer bypasses ``check_open_window`` (issue #106).
    """
    from sqlalchemy import select
    from club_server.db.models.event import Event
    from club_server.db.models.enrollment import Enrollment

    past_start = past_time_ms(hours_ago)
    past_end = past_start + 60 * 60 * 1000
    event_row = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event_row.start_time = past_start
    event_row.end_time = past_end
    for username in usernames:
        enrollment_row = (
            await db_session.execute(
                select(Enrollment).where(
                    Enrollment.event_id == event_id,
                    Enrollment.membername == username,
                )
            )
        ).scalar_one()
        enrollment_row.enrolled_at = past_start - 1
    await db_session.flush()
    return past_start


@pytest.mark.asyncio
async def test_mark_attendance(client: AsyncClient, db_session: AsyncSession):
    """Test marking attendance for a user."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={
            "records": [
                {"membername": "testuser", "status": "present", "notes": "On time"}
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.status_code == 200
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["membername"] == "testuser"
    assert records[0]["status"] == "present"
    assert records[0]["notes"] == "On time"


@pytest.mark.asyncio
async def test_mark_attendance_update(client: AsyncClient, db_session: AsyncSession):
    """Test updating existing attendance record."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={
            "records": [
                {
                    "membername": "testuser",
                    "status": "late",
                    "notes": "Arrived 15 min late",
                }
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["status"] == "late"


@pytest.mark.asyncio
async def test_bulk_mark_attendance(client: AsyncClient, db_session: AsyncSession):
    """Test bulk marking attendance for multiple users."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    start_time = await move_event_to_recent_past(
        db_session, event_id, ["user1", "user2"]
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        json={
            "records": [
                {"membername": "user1", "status": "present"},
                {"membername": "user2", "status": "absent"},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    assert len(records) == 2


@pytest.mark.asyncio
async def test_list_user_attendance_returns_records(
    client: AsyncClient, db_session: AsyncSession
):
    """Test listing attendance records for a user returns correct data."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={
            "records": [
                {"membername": "testuser", "status": "present", "notes": "On time"}
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 1
    assert records[0]["eventId"] == event_id
    assert records[0]["occurrenceTimeUtc"] == occurrence_time_utc
    assert records[0]["membername"] == "testuser"
    assert records[0]["status"] == "present"
    assert records[0]["notes"] == "On time"
    assert "id" not in records[0]


@pytest.mark.asyncio
async def test_list_user_attendance_empty_when_no_records(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that listing attendance returns empty list when no records exist."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    _ = await create_event_and_enroll(client, admin_token, "testuser")

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_user_attendance_empty_when_no_enrollments(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that listing attendance returns empty list when user has no enrollments."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_user_attendance_filters_by_date_range(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that attendance records are filtered by the date range."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    venue_id = await create_venue(client, admin_token)
    # Create both events in the future to pass validation, then move them to
    # the past in distinct windows so the date-range filter has something to
    # discriminate. Marking attendance requires the open window to be open.
    event1_resp = await client.post(
        "/v1/events",
        json={
            "title": "Event In Range",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event1_id = event1_resp.json()["id"]

    event2_resp = await client.post(
        "/v1/events",
        json={
            "title": "Event Out of Range",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(72),
            "endTimeUtc": future_time_ms(73),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event2_id = event2_resp.json()["id"]

    for eid in [event1_id, event2_id]:
        await client.post(
            f"/v1/events/by_id/{eid}/enrollments/assign",
            json={"membernames": ["testuser"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    time_in_range = await move_event_to_recent_past(
        db_session, event1_id, ["testuser"], hours_ago=1
    )
    time_out_of_range = await move_event_to_recent_past(
        db_session, event2_id, ["testuser"], hours_ago=5 * 24
    )

    for eid, occ_time in [(event1_id, time_in_range), (event2_id, time_out_of_range)]:
        await client.post(
            f"/v1/events/by_id/{eid}/occurrences/{occ_time}/attendance",
            json={"records": [{"membername": "testuser", "status": "present"}]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    from_time = int(
        (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000
    )
    to_time = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 1
    assert records[0]["eventId"] == event1_id


@pytest.mark.asyncio
async def test_list_user_attendance_only_enrolled_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that only records from enrolled events are returned."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    _ = await create_user_and_get_token(client, admin_token, "otheruser", db_session)

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)

    event1_resp = await client.post(
        "/v1/events",
        json={
            "title": "Enrolled Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": start_time + 3600000,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event1_id = event1_resp.json()["id"]

    event2_resp = await client.post(
        "/v1/events",
        json={
            "title": "Not Enrolled Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time + 7200000,
            "endTimeUtc": start_time + 10800000,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event2_id = event2_resp.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event1_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/events/by_id/{event2_id}/enrollments/assign",
        json={"membernames": ["otheruser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    occ1 = await move_event_to_recent_past(db_session, event1_id, ["testuser"])
    occ2 = await move_event_to_recent_past(
        db_session, event2_id, ["otheruser"], hours_ago=2
    )

    await client.post(
        f"/v1/events/by_id/{event1_id}/occurrences/{occ1}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/events/by_id/{event2_id}/occurrences/{occ2}/attendance",
        json={"records": [{"membername": "otheruser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 1
    assert records[0]["eventId"] == event1_id
    assert records[0]["membername"] == "testuser"


@pytest.mark.asyncio
async def test_list_user_attendance_range_too_large(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that date range exceeding 365 days returns 400."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    from_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=400)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "RANGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_declare_leave(client: AsyncClient, db_session: AsyncSession):
    """Test declaring leave for an occurrence."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical appointment"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["status"] == "onLeaveRequested"
    assert records[0]["leaveReason"] == "Medical appointment"


@pytest.mark.asyncio
async def test_declare_leave_window_closed(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that leave cannot be declared less than 2 hours before occurrence."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    soon_time = int(
        (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000
    )
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": soon_time,
            "endTimeUtc": int(
                (datetime.now(timezone.utc) + timedelta(hours=2)).timestamp() * 1000
            ),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{soon_time}/leave/request",
        json={"reason": "Emergency"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert "LEAVE_WINDOW_CLOSED" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_cancel_leave(client: AsyncClient, db_session: AsyncSession):
    """Test cancelling a leave request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/cancel",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.json() == []


@pytest.mark.asyncio
async def test_approve_leave(client: AsyncClient, db_session: AsyncSession):
    """Test approving a leave request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["status"] == "onLeave"


@pytest.mark.asyncio
async def test_reject_leave(client: AsyncClient, db_session: AsyncSession):
    """Test rejecting a leave request."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/reject",
        json={"membernames": ["testuser"], "reason": "Important session"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.json() == []


@pytest.mark.asyncio
async def test_mark_attendance_without_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that attendance can be marked for users even without formal enrollment.

    Note: The system allows marking attendance without enrollment for flexibility.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

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
    start_time = await move_event_to_recent_past(db_session, event_id, [])

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_attendance_requires_auth(client: AsyncClient):
    """Test that attendance endpoints require authentication."""
    response = await client.get(
        "/v1/events/by_id/1/occurrences/1234567890000/attendance"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_cannot_mark_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that regular users cannot mark attendance."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_user_cannot_declare_leave_for_others(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that users cannot declare leave for others."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Test Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_declare_leave_already_declared_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that leave cannot be declared twice."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Another reason"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 409
    assert "LEAVE_ALREADY_DECLARED" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_cancel_leave_not_pending_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that only pending leave can be cancelled."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/cancel",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert "INVALID_STATE" in str(response.json()["detail"])


async def create_past_event_and_enroll(
    client: AsyncClient,
    admin_token: str,
    username: str,
    days_ago: int = 20,
    db_session: AsyncSession | None = None,
) -> tuple[int, int]:
    """Helper to create an event, enroll user, then move event to the past.

    Creates a future event first (to pass enrollment validation), assigns the
    user, then moves timestamps to the past via direct DB update.
    """
    from sqlalchemy import select
    from club_server.db.models.event import Event

    venue_id = await create_venue(client, admin_token)
    past_start = int(
        (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp() * 1000
    )
    past_end = int(
        (
            datetime.now(timezone.utc) - timedelta(days=days_ago) + timedelta(hours=1)
        ).timestamp()
        * 1000
    )

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Past Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Move event to the past via direct DB update
    assert db_session is not None, "db_session required to move event to the past"
    result = await db_session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one()
    event.start_time = past_start
    event.end_time = past_end
    # Backfill enrolled_at so the enrollment temporally covers the past
    # occurrence (assignment happens "now" but the occurrence is in the past).
    from club_server.db.models.enrollment import Enrollment

    enrollment_result = await db_session.execute(
        select(Enrollment).where(
            Enrollment.event_id == event_id,
            Enrollment.membername == username,
        )
    )
    enrollment = enrollment_result.scalar_one()
    enrollment.enrolled_at = past_start - 1
    await db_session.flush()

    return event_id, past_start


@pytest.mark.asyncio
async def test_mark_attendance_edit_window_closed(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that marking attendance is rejected after the 15-day edit window."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=20, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"


@pytest.mark.asyncio
async def test_bulk_mark_attendance_edit_window_closed(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that bulk marking attendance is rejected after the 15-day edit window."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=20, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"


@pytest.mark.asyncio
async def test_approve_leave_edit_window_closed(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that approving leave is rejected after the 15-day edit window."""
    from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
    from club_server.utils import now_utc_ms
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=20, db_session=db_session
    )

    record = AttendanceRecord(
        event_id=event_id,
        occurrence_time_utc=occurrence_time_utc,
        membername="testuser",
        status=AttendanceStatus.on_leave_requested.value,
        leave_reason="Medical",
        recorded_at=now_utc_ms(),
    )
    db_session.add(record)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"


@pytest.mark.asyncio
async def test_reject_leave_edit_window_closed(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that rejecting leave is rejected after the 15-day edit window."""
    from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
    from club_server.utils import now_utc_ms
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=20, db_session=db_session
    )

    record = AttendanceRecord(
        event_id=event_id,
        occurrence_time_utc=occurrence_time_utc,
        membername="testuser",
        status=AttendanceStatus.on_leave_requested.value,
        leave_reason="Medical",
        recorded_at=now_utc_ms(),
    )
    db_session.add(record)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/reject",
        json={"membernames": ["testuser"], "reason": "Not approved"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"


@pytest.mark.asyncio
async def test_mark_attendance_within_edit_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that marking attendance within the 15-day window succeeds."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=10, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_super_admin_bypasses_edit_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that super admin can mark attendance after edit window closes."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, admin_token, "testuser", days_ago=20, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_regular_user_cannot_view_others_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot view another user's attendance."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/user2/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_user_can_view_own_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a user can view their own attendance."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_view_others_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that an admin can view another user's attendance."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_coach_can_view_others_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a coach can view another user's attendance."""
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/myevents/by_id/testuser/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200


# =============================================================================
# Admin attendance endpoint tests
# =============================================================================


@pytest.mark.asyncio
async def test_admin_list_all_attendance(client: AsyncClient, db_session: AsyncSession):
    """Test that admin can list all attendance records across events."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "user1", db_session)
    _ = await create_user_and_get_token(client, admin_token, "user2", db_session)

    venue_id = await create_venue(client, admin_token)

    event1_resp = await client.post(
        "/v1/events",
        json={
            "title": "Event 1",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event1_id = event1_resp.json()["id"]

    event2_resp = await client.post(
        "/v1/events",
        json={
            "title": "Event 2",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(26),
            "endTimeUtc": future_time_ms(27),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event2_id = event2_resp.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event1_id}/enrollments/assign",
        json={"membernames": ["user1"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/events/by_id/{event2_id}/enrollments/assign",
        json={"membernames": ["user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    occ1 = await move_event_to_recent_past(db_session, event1_id, ["user1"])
    occ2 = await move_event_to_recent_past(
        db_session, event2_id, ["user2"], hours_ago=2
    )

    await client.post(
        f"/v1/events/by_id/{event1_id}/occurrences/{occ1}/attendance",
        json={"records": [{"membername": "user1", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/events/by_id/{event2_id}/occurrences/{occ2}/attendance",
        json={"records": [{"membername": "user2", "status": "late"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)

    response = await client.get(
        f"/v1/events/occurrences/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 2
    membernames = {r["membername"] for r in records}
    assert membernames == {"user1", "user2"}


@pytest.mark.asyncio
async def test_admin_list_attendance_empty(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that admin attendance list returns empty when no records exist."""
    admin_token = await create_admin_user(db_session)

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/events/occurrences/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_admin_list_attendance_filters_by_date_range(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that admin attendance list filters by date range."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    venue_id = await create_venue(client, admin_token)
    event1_resp = await client.post(
        "/v1/events",
        json={
            "title": "Event In Range",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event1_id = event1_resp.json()["id"]

    event2_resp = await client.post(
        "/v1/events",
        json={
            "title": "Event Out of Range",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(72),
            "endTimeUtc": future_time_ms(73),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event2_id = event2_resp.json()["id"]

    for eid in [event1_id, event2_id]:
        await client.post(
            f"/v1/events/by_id/{eid}/enrollments/assign",
            json={"membernames": ["testuser"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    time_in_range = await move_event_to_recent_past(
        db_session, event1_id, ["testuser"], hours_ago=1
    )
    time_out_of_range = await move_event_to_recent_past(
        db_session, event2_id, ["testuser"], hours_ago=5 * 24
    )

    await client.post(
        f"/v1/events/by_id/{event1_id}/occurrences/{time_in_range}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/events/by_id/{event2_id}/occurrences/{time_out_of_range}/attendance",
        json={"records": [{"membername": "testuser", "status": "absent"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    from_time = int(
        (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000
    )
    to_time = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/events/occurrences/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 1
    assert records[0]["eventId"] == event1_id


@pytest.mark.asyncio
async def test_admin_list_attendance_range_too_large(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that admin attendance list rejects range exceeding 365 days."""
    admin_token = await create_admin_user(db_session)

    from_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=400)).timestamp() * 1000)

    response = await client.get(
        f"/v1/events/occurrences/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "RANGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_regular_user_cannot_list_all_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a regular user cannot access the admin attendance endpoint."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/events/occurrences/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_coach_can_list_all_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that a coach can access the admin attendance endpoint."""
    from .helpers import create_coach_user

    coach_token = await create_coach_user(db_session)

    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)

    response = await client.get(
        f"/v1/events/occurrences/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200


# =============================================================================
# Fix #1: mark_attendance status validation
# =============================================================================


@pytest.mark.asyncio
async def test_mark_attendance_rejects_on_leave_status(
    client: AsyncClient, db_session: AsyncSession
):
    """mark_attendance must reject onLeave — force proper leave workflow."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "onLeave"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ATTENDANCE_STATUS"


@pytest.mark.asyncio
async def test_mark_attendance_rejects_on_leave_requested_status(
    client: AsyncClient, db_session: AsyncSession
):
    """mark_attendance must reject onLeaveRequested — force proper leave workflow."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "onLeaveRequested"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ATTENDANCE_STATUS"


@pytest.mark.asyncio
async def test_mark_attendance_rejects_invalid_string(
    client: AsyncClient, db_session: AsyncSession
):
    """mark_attendance must reject arbitrary strings."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "invalid_status"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ATTENDANCE_STATUS"


@pytest.mark.asyncio
async def test_mark_attendance_allows_valid_statuses(
    client: AsyncClient, db_session: AsyncSession
):
    """mark_attendance must accept present, absent, late."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    for valid_status in ["present", "absent", "late"]:
        response = await client.post(
            f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time}/attendance",
            json={"records": [{"membername": "testuser", "status": valid_status}]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200


# =============================================================================
# Fix #4: reject_leave restores previous_status
# =============================================================================


@pytest.mark.asyncio
async def test_reject_leave_restores_previous_status(
    client: AsyncClient, db_session: AsyncSession
):
    """reject_leave should restore previous attendance status if one exists."""
    from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
    from club_server.utils import now_utc_ms

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    # Seed a "present" record directly: marking via API is no longer allowed
    # on future events (issue #106), but the reject_leave restoration logic is
    # independent of how the prior record came to exist.
    db_session.add(
        AttendanceRecord(
            event_id=event_id,
            occurrence_time_utc=occurrence_time,
            membername="testuser",
            status=AttendanceStatus.present.value,
            recorded_at=now_utc_ms(),
        )
    )
    await db_session.commit()

    # Declare leave (event is still in the future, leave window open).
    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    # Reject the leave
    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time}/leave/reject",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Verify status restored to present
    att_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert att_response.status_code == 200
    assert att_response.json()["status"] == "present"


@pytest.mark.asyncio
async def test_reject_leave_deletes_when_no_previous(
    client: AsyncClient, db_session: AsyncSession
):
    """reject_leave should delete record when leave was first attendance record."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    # Declare leave without prior attendance
    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    # Reject the leave
    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time}/leave/reject",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Verify no attendance record exists
    att_response = await client.get(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert att_response.status_code == 200
    assert att_response.json() is None


# =============================================================================
# Fix #5: Super admin bypass for leave window
# =============================================================================


@pytest.mark.asyncio
async def test_super_admin_bypasses_leave_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Super admin can declare leave even when the 2-hour window has closed."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    venue_id = await create_venue(client, admin_token)
    # Create event with occurrence in the past (1 hour from now — inside the 2h window)
    start_time = past_time_ms(1)
    end_time = start_time + 3600000

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Past Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Super admin declares leave on behalf of user — should bypass window
    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Emergency"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_regular_user_blocked_by_leave_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Non-super-admin user is still blocked by the 2-hour leave window."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    # Occurrence 1 hour in the future — inside the 2h leave-declaration window.
    start_time = future_time_ms(1)
    end_time = start_time + 3600000

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Imminent Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Emergency"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LEAVE_WINDOW_CLOSED"


# =============================================================================
# Bulk leave operations and multi-user/multi-occurrence scenarios
# =============================================================================


@pytest.mark.asyncio
async def test_bulk_approve_leave(client: AsyncClient, db_session: AsyncSession):
    """Test approving multiple leave requests at once."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    end_time = future_time_ms(25)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Team Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Doctor visit"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Family event"},
        headers={"Authorization": f"Bearer {user2_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/leave/approve",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    statuses = {r["membername"]: r["status"] for r in records}
    assert statuses["user1"] == "onLeave"
    assert statuses["user2"] == "onLeave"


@pytest.mark.asyncio
async def test_bulk_reject_leave(client: AsyncClient, db_session: AsyncSession):
    """Test rejecting multiple leave requests at once."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    end_time = future_time_ms(25)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Team Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Doctor visit"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Family event"},
        headers={"Authorization": f"Bearer {user2_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/leave/reject",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    # After rejection, records should be removed (no previous status)
    assert len(records) == 0


@pytest.mark.asyncio
async def test_declare_leave_different_occurrences(
    client: AsyncClient, db_session: AsyncSession
):
    """Test same user declaring leave on two different occurrences."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    # Both leave times must be real occurrences of the daily camp (#470):
    # its slots are whole seconds, one day apart.
    start_time_1 = future_time_ms(24) // 1000 * 1000
    end_time_1 = start_time_1 + 60 * 60 * 1000
    start_time_2 = start_time_1 + 24 * 60 * 60 * 1000

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Daily Training",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_time_1,
            "endTimeUtc": end_time_1,
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response1 = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time_1}/leave/request",
        json={"reason": "Medical day 1"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response1.status_code == 204

    response2 = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{start_time_2}/leave/request",
        json={"reason": "Medical day 2"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response2.status_code == 204

    att1 = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time_1}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert att1.json()[0]["status"] == "onLeaveRequested"

    att2 = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time_2}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert att2.json()[0]["status"] == "onLeaveRequested"


@pytest.mark.asyncio
async def test_multiple_users_declare_leave_same_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """Test multiple users declaring leave on the same occurrence."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session
    )

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    end_time = future_time_ms(25)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Team Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["user1", "user2"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response1 = await client.post(
        f"/v1/myevents/by_id/user1/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response1.status_code == 204

    response2 = await client.post(
        f"/v1/myevents/by_id/user2/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Travel"},
        headers={"Authorization": f"Bearer {user2_token}"},
    )
    assert response2.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    statuses = {r["membername"]: r["status"] for r in records}
    assert statuses["user1"] == "onLeaveRequested"
    assert statuses["user2"] == "onLeaveRequested"


@pytest.mark.asyncio
async def test_declare_leave_with_reason(client: AsyncClient, db_session: AsyncSession):
    """Test that leave reason is stored and returned in attendance record."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Family wedding"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["leaveReason"] == "Family wedding"


# ---------------------------------------------------------------------------
# Issue #157: enrollment-eligibility checks on attendance mutations.
# ---------------------------------------------------------------------------


async def _open_window_for_occurrence(
    db_session: AsyncSession, event_id: int, occurrence_time_utc: int
) -> None:
    """Install an OccurrenceOverride pulling the effective start inside the
    30-min open window. Used by tests whose subject is something other than
    the open-window gate so they can run against non-super-admin callers."""
    from club_server.db.models.occurrence_override import OccurrenceOverride
    from sqlalchemy import select as _sel

    new_start = int(
        (datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp() * 1000
    )
    existing = await db_session.execute(
        _sel(OccurrenceOverride).where(
            OccurrenceOverride.event_id == event_id,
            OccurrenceOverride.occurrence_time == occurrence_time_utc,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        db_session.add(
            OccurrenceOverride(
                event_id=event_id,
                occurrence_time=occurrence_time_utc,
                status="rescheduled",
                new_start_time=new_start,
            )
        )
    else:
        row.status = "rescheduled"
        row.new_start_time = new_start
    await db_session.commit()


async def _create_event_no_enrollment(
    client: AsyncClient, admin_token: str
) -> tuple[int, int]:
    """Create a future event without enrolling anyone."""
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
    return event_response.json()["id"], start_time


async def _set_enrollment_state(
    db_session: AsyncSession,
    event_id: int,
    membername: str,
    *,
    status: str | None = None,
    enrolled_at: int | None = None,
    withdrawn_at: int | None = None,
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
    if status is not None:
        enrollment.status = status
    enrollment.enrolled_at = enrolled_at
    enrollment.withdrawn_at = withdrawn_at
    await db_session.flush()


async def _insert_pending_leave(
    db_session: AsyncSession,
    event_id: int,
    occurrence_time_utc: int,
    membername: str,
    previous_status: str | None = None,
) -> None:
    from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
    from club_server.utils import now_utc_ms

    record = AttendanceRecord(
        event_id=event_id,
        occurrence_time_utc=occurrence_time_utc,
        membername=membername,
        status=AttendanceStatus.on_leave_requested.value,
        previous_status=previous_status,
        recorded_at=now_utc_ms(),
    )
    db_session.add(record)
    await db_session.flush()


# --- mark_attendance ---


@pytest.mark.asyncio
async def test_mark_attendance_rejects_unenrolled_user(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _open_window_for_occurrence(db_session, event_id, occ_time)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "stranger", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_mark_attendance_super_admin_bypasses_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _open_window_for_occurrence(db_session, event_id, occ_time)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "stranger", "status": "present"}]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_response.status_code == 200
    records = get_response.json()
    assert any(
        r["membername"] == "stranger" and r["status"] == "present" for r in records
    )


@pytest.mark.asyncio
async def test_mark_attendance_rejects_withdrawn_member(
    client: AsyncClient, db_session: AsyncSession
):
    from club_server.db.models.enrollment import EnrollmentStatus
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "alice", db_session)
    event_id, occ_time = await create_event_and_enroll(
        client, super_admin_token, "alice"
    )
    # Withdraw alice — future occurrence becomes ineligible.
    await _set_enrollment_state(
        db_session,
        event_id,
        "alice",
        status=EnrollmentStatus.withdrawn.value,
        enrolled_at=past_time_ms(48),
        withdrawn_at=past_time_ms(1),
    )
    await _open_window_for_occurrence(db_session, event_id, occ_time)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_mark_attendance_allows_withdrawal_after_past_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """Member enrolled, attended past occurrence, withdrew later — still eligible."""
    from club_server.db.models.enrollment import EnrollmentStatus
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "bob", db_session)
    event_id, occ_time = await create_past_event_and_enroll(
        client, super_admin_token, "bob", days_ago=3, db_session=db_session
    )
    # Bob withdrew AFTER the past occurrence.
    await _set_enrollment_state(
        db_session,
        event_id,
        "bob",
        status=EnrollmentStatus.withdrawn.value,
        enrolled_at=past_time_ms(24 * 5),
        withdrawn_at=past_time_ms(24),  # 1 day ago — after the 3-day-old occurrence
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "bob", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_mark_attendance_rejects_declined_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """Declined enrollment never participated — enrolled_at is null."""
    from club_server.db.models.enrollment import EnrollmentStatus
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(client, super_admin_token, "carol", db_session)
    event_id, occ_time = await create_past_event_and_enroll(
        client, super_admin_token, "carol", days_ago=3, db_session=db_session
    )
    await _set_enrollment_state(
        db_session,
        event_id,
        "carol",
        status=EnrollmentStatus.declined.value,
        enrolled_at=None,
        withdrawn_at=None,
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "carol", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


# --- declare_leave (self-service) ---


@pytest.mark.asyncio
async def test_declare_leave_rejects_unenrolled_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, admin_token)

    response = await client.post(
        f"/v1/myevents/by_id/stranger/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_declare_leave_super_admin_bypasses_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)

    response = await client.post(
        f"/v1/myevents/by_id/stranger/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_declare_leave_rejects_withdrawn_member(
    client: AsyncClient, db_session: AsyncSession
):
    from club_server.db.models.enrollment import EnrollmentStatus

    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    event_id, occ_time = await create_event_and_enroll(client, admin_token, "alice")
    await _set_enrollment_state(
        db_session,
        event_id,
        "alice",
        status=EnrollmentStatus.withdrawn.value,
        enrolled_at=past_time_ms(48),
        withdrawn_at=past_time_ms(1),
    )

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


# --- approve_leave ---


@pytest.mark.asyncio
async def test_approve_leave_rejects_unenrolled_user(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _insert_pending_leave(db_session, event_id, occ_time, "stranger")

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/approve",
        json={"membernames": ["stranger"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_approve_leave_super_admin_bypasses_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _insert_pending_leave(db_session, event_id, occ_time, "stranger")

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/approve",
        json={"membernames": ["stranger"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


# --- reject_leave ---


@pytest.mark.asyncio
async def test_reject_leave_rejects_unenrolled_user(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _insert_pending_leave(db_session, event_id, occ_time, "stranger")

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/reject",
        json={"membernames": ["stranger"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_reject_leave_super_admin_bypasses_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _insert_pending_leave(db_session, event_id, occ_time, "stranger")

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/reject",
        json={"membernames": ["stranger"]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


# --- cancel_leave (self-service) ---


@pytest.mark.asyncio
async def test_cancel_leave_rejects_unenrolled_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, admin_token)
    await _insert_pending_leave(db_session, event_id, occ_time, "stranger")

    response = await client.post(
        f"/v1/myevents/by_id/stranger/{event_id}/occurrences/{occ_time}/leave/cancel",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_cancel_leave_super_admin_bypasses_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "stranger", db_session
    )
    event_id, occ_time = await _create_event_no_enrollment(client, super_admin_token)
    await _insert_pending_leave(db_session, event_id, occ_time, "stranger")

    response = await client.post(
        f"/v1/myevents/by_id/stranger/{event_id}/occurrences/{occ_time}/leave/cancel",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# Issue #158: only admin or event organizer may mutate attendance.
# ---------------------------------------------------------------------------


async def _create_event_with_organizer(
    client: AsyncClient,
    admin_token: str,
    *,
    organizer_username: str,
    member_username: str,
) -> tuple[int, int]:
    """Create a future event with an explicit organizer and one enrolled member."""
    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)
    end_time = future_time_ms(25)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Organized Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": end_time,
            "organizerName": organizer_username,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert event_response.status_code == 201, event_response.text
    event_id = event_response.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [member_username]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return event_id, start_time


@pytest.mark.asyncio
async def test_mark_attendance_rejects_non_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "organizer_coach")
    other_coach_token = await create_coach_user(db_session, "other_coach")
    _ = await create_user_and_get_token(client, admin_token, "alice", db_session)
    event_id, occ_time = await _create_event_with_organizer(
        client,
        admin_token,
        organizer_username="organizer_coach",
        member_username="alice",
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers={"Authorization": f"Bearer {other_coach_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_mark_attendance_allowed_for_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    organizer_token = await create_coach_user(db_session, "organizer_coach")
    _ = await create_user_and_get_token(client, admin_token, "alice", db_session)
    event_id, occ_time = await _create_event_with_organizer(
        client,
        admin_token,
        organizer_username="organizer_coach",
        member_username="alice",
    )
    await _open_window_for_occurrence(db_session, event_id, occ_time)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers={"Authorization": f"Bearer {organizer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_approve_leave_rejects_non_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "organizer_coach")
    other_coach_token = await create_coach_user(db_session, "other_coach")
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    event_id, occ_time = await _create_event_with_organizer(
        client,
        admin_token,
        organizer_username="organizer_coach",
        member_username="alice",
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/approve",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {other_coach_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_approve_leave_allowed_for_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    organizer_token = await create_coach_user(db_session, "organizer_coach")
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    event_id, occ_time = await _create_event_with_organizer(
        client,
        admin_token,
        organizer_username="organizer_coach",
        member_username="alice",
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/approve",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {organizer_token}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_reject_leave_rejects_non_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "organizer_coach")
    other_coach_token = await create_coach_user(db_session, "other_coach")
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    event_id, occ_time = await _create_event_with_organizer(
        client,
        admin_token,
        organizer_username="organizer_coach",
        member_username="alice",
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/reject",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {other_coach_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.asyncio
async def test_reject_leave_allowed_for_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    admin_token = await create_admin_user(db_session)
    organizer_token = await create_coach_user(db_session, "organizer_coach")
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    event_id, occ_time = await _create_event_with_organizer(
        client,
        admin_token,
        organizer_username="organizer_coach",
        member_username="alice",
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ_time}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/leave/reject",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {organizer_token}"},
    )
    assert response.status_code == 204


# =============================================================================
# Issue #26: close attendance test-coverage gaps
# =============================================================================


# --- R17: 404 EVENT_NOT_FOUND on attendance writes ---


@pytest.mark.asyncio
async def test_mark_attendance_event_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """R17: marking attendance against a non-existent event must return 404 EVENT_NOT_FOUND."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)

    missing_event_id = 999_999
    occurrence_time_utc = future_time_ms(24)

    response = await client.post(
        f"/v1/events/by_id/{missing_event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"


# --- R19: 404 ATTENDANCE_NOT_FOUND on cancel / approve / reject ---


@pytest.mark.asyncio
async def test_cancel_leave_no_record_404(
    client: AsyncClient, db_session: AsyncSession
):
    """R19: cancelling leave with no existing record must return 404 ATTENDANCE_NOT_FOUND."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/cancel",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_approve_leave_no_record_404(
    client: AsyncClient, db_session: AsyncSession
):
    """R19: approving leave with no existing record must return 404 ATTENDANCE_NOT_FOUND."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_reject_leave_no_record_404(
    client: AsyncClient, db_session: AsyncSession
):
    """R19: rejecting leave with no existing record must return 404 ATTENDANCE_NOT_FOUND."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/reject",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_FOUND"


# --- R25: mark_attendance overwrites a leave-status row ---


@pytest.mark.asyncio
async def test_mark_attendance_overwrites_leave_status(
    client: AsyncClient, db_session: AsyncSession
):
    """R25: marking attendance over an onLeaveRequested row succeeds and rolls
    the leave status into previous_status."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    leave_response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Sick"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert leave_response.status_code == 204

    pre = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert pre.status_code == 200
    pre_records = pre.json()
    assert len(pre_records) == 1
    assert pre_records[0]["status"] == "onLeaveRequested"

    # Pull the effective start into the open window so attendance marking is
    # allowed (super-admin no longer bypasses check_open_window — issue #106).
    await _open_window_for_occurrence(db_session, event_id, occurrence_time_utc)

    mark_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mark_response.status_code == 200

    post = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert post.status_code == 200
    records = post.json()
    assert len(records) == 1
    assert records[0]["status"] == "present"
    assert records[0]["previousStatus"] == "onLeaveRequested"


# --- R32: approve/reject leave from wrong status -> 422 INVALID_STATE ---


@pytest.mark.asyncio
async def test_approve_leave_wrong_status_422(
    client: AsyncClient, db_session: AsyncSession
):
    """R32: approving leave when the row is not onLeaveRequested must return 422 INVALID_STATE."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    mark_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mark_response.status_code == 200

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_reject_leave_wrong_status_422(
    client: AsyncClient, db_session: AsyncSession
):
    """R32: rejecting leave when the row is onLeave (already approved) must return 422."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    _ = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    approve_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve_response.status_code == 204

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/reject",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"


# --- R39 / R40 / R41: audit-log assertions ---


async def _audit_actions_for_occurrence(
    db_session: AsyncSession, event_id: int, occurrence_time_utc: int
) -> list[str]:
    from sqlalchemy import select as _sa_select

    from club_server.db.models.audit_log import AuditLog

    rows = (
        await db_session.execute(
            _sa_select(AuditLog.action).where(
                AuditLog.resource_type == "occurrence",
                AuditLog.resource_id == f"{event_id}:{occurrence_time_utc}",
            )
        )
    ).all()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_mark_attendance_writes_audit_entry(
    client: AsyncClient, db_session: AsyncSession
):
    """R39: a successful mark_attendance produces an audit row keyed by event:occurrence."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    actions = await _audit_actions_for_occurrence(
        db_session, event_id, occurrence_time_utc
    )
    assert actions.count("attendance_marked") == 1


@pytest.mark.asyncio
async def test_clear_attendance_writes_audit_entry(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue #251: a successful clear writes exactly one audit row, atomically.

    The audit write shares the request transaction with the delete, so it is
    committed iff the clear is. A failed clear (404) rolls back and leaves no
    audit row behind.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occ_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    mark = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mark.status_code == 200

    clear = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert clear.status_code == 204

    actions = await _audit_actions_for_occurrence(db_session, event_id, occ_time)
    assert actions.count("attendance_cleared") == 1

    # A second clear has nothing to remove (404) and must not write an audit row.
    repeat = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert repeat.status_code == 404

    actions = await _audit_actions_for_occurrence(db_session, event_id, occ_time)
    assert actions.count("attendance_cleared") == 1


@pytest.mark.asyncio
async def test_leave_decision_writes_audit_entry(
    client: AsyncClient, db_session: AsyncSession
):
    """R40: declare / cancel / approve / reject each write an audit row with the
    appropriate action keyed by event:occurrence."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )

    venue_id = await create_venue(client, admin_token)
    start_time_a = future_time_ms(24)
    start_time_b = future_time_ms(48)
    start_time_c = future_time_ms(72)

    async def make_event(title: str, start: int) -> int:
        resp = await client.post(
            "/v1/events",
            json={
                "title": title,
                "type": "programme",
                "venueId": venue_id,
                "startTimeUtc": start,
                "endTimeUtc": start + 3600000,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        eid = resp.json()["id"]
        _ = await client.post(
            f"/v1/events/by_id/{eid}/enrollments/assign",
            json={"membernames": ["testuser"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        return eid

    event_id_a = await make_event("Audit Event A", start_time_a)
    event_id_b = await make_event("Audit Event B", start_time_b)
    event_id_c = await make_event("Audit Event C", start_time_c)

    # A: declare + cancel
    assert (
        await client.post(
            f"/v1/myevents/by_id/testuser/{event_id_a}/occurrences/{start_time_a}/leave/request",
            json={"reason": "x"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 204
    assert (
        await client.post(
            f"/v1/myevents/by_id/testuser/{event_id_a}/occurrences/{start_time_a}/leave/cancel",
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 204
    actions_a = await _audit_actions_for_occurrence(
        db_session, event_id_a, start_time_a
    )
    assert "leave_requested" in actions_a
    assert "leave_cancelled" in actions_a

    # B: declare + approve
    assert (
        await client.post(
            f"/v1/myevents/by_id/testuser/{event_id_b}/occurrences/{start_time_b}/leave/request",
            json={"reason": "y"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 204
    assert (
        await client.post(
            f"/v1/events/by_id/{event_id_b}/occurrences/{start_time_b}/leave/approve",
            json={"membernames": ["testuser"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).status_code == 204
    actions_b = await _audit_actions_for_occurrence(
        db_session, event_id_b, start_time_b
    )
    assert "leave_requested" in actions_b
    assert "leave_approved" in actions_b

    # C: declare + reject
    assert (
        await client.post(
            f"/v1/myevents/by_id/testuser/{event_id_c}/occurrences/{start_time_c}/leave/request",
            json={"reason": "z"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
    ).status_code == 204
    assert (
        await client.post(
            f"/v1/events/by_id/{event_id_c}/occurrences/{start_time_c}/leave/reject",
            json={"membernames": ["testuser"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).status_code == 204
    actions_c = await _audit_actions_for_occurrence(
        db_session, event_id_c, start_time_c
    )
    assert "leave_requested" in actions_c
    assert "leave_rejected" in actions_c


@pytest.mark.asyncio
async def test_failed_attendance_mutation_writes_no_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """R41: a failed attendance mutation (approve with no record) must not leave
    an audit row keyed by that occurrence, and must not change total audit-log size."""
    from sqlalchemy import select as _sa_select

    from club_server.db.models.audit_log import AuditLog

    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occurrence_time_utc = await create_event_and_enroll(
        client, admin_token, "testuser"
    )

    pre_count = (await db_session.execute(_sa_select(AuditLog.id))).all()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_FOUND"

    actions = await _audit_actions_for_occurrence(
        db_session, event_id, occurrence_time_utc
    )
    assert "leave_approved" not in actions

    post_count = (await db_session.execute(_sa_select(AuditLog.id))).all()
    assert len(post_count) == len(pre_count)


# =============================================================================
# Attendance open-window (#22) — gate marking to ≥30 min before effective start.
# Effective start = OccurrenceOverride.new_start_time when set, else slot key.
# =============================================================================


async def _create_future_event_and_enroll(
    client: AsyncClient,
    admin_token: str,
    username: str,
    minutes_from_now: int,
) -> tuple[int, int]:
    """Create an event whose start is `minutes_from_now` minutes from now and
    enroll `username`. Returns (event_id, occurrence_time_utc)."""
    venue_id = await create_venue(client, admin_token)
    start_ms = int(
        (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).timestamp()
        * 1000
    )
    end_ms = start_ms + 60 * 60 * 1000
    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Future Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return event_id, start_ms


async def _set_override(
    db_session: AsyncSession,
    event_id: int,
    occurrence_time_utc: int,
    new_start_time: int,
) -> None:
    """Insert/update an OccurrenceOverride row pinning new_start_time."""
    from club_server.db.models.occurrence_override import OccurrenceOverride
    from sqlalchemy import select as _sel

    existing = await db_session.execute(
        _sel(OccurrenceOverride).where(
            OccurrenceOverride.event_id == event_id,
            OccurrenceOverride.occurrence_time == occurrence_time_utc,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = OccurrenceOverride(
            event_id=event_id,
            occurrence_time=occurrence_time_utc,
            status="rescheduled",
            new_start_time=new_start_time,
        )
        db_session.add(row)
    else:
        row.status = "rescheduled"
        row.new_start_time = new_start_time
    await db_session.flush()


@pytest.mark.asyncio
async def test_mark_attendance_rejected_before_open_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Non-super-admin cannot mark attendance more than 30 min before start."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    # Occurrence 31 minutes in the future — open window has not started yet.
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=31
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_YET_OPEN"


@pytest.mark.asyncio
async def test_mark_attendance_allowed_within_open_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Non-super-admin can mark attendance at start − 30 min or later."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    # Occurrence 20 minutes in the future — inside the 30-min open window.
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=20
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    # Double-verification: record is queryable via reader API.
    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.status_code == 200
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["membername"] == "testuser"
    assert records[0]["status"] == "present"


@pytest.mark.asyncio
async def test_super_admin_rejected_before_open_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Super-admin cannot fabricate attendance for a future occurrence.

    Sudo's intended power is to correct the audit trail after the edit window
    closes, not to mark attendance for sessions that have not yet happened.
    """
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=120
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_YET_OPEN"

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json() == []


@pytest.mark.asyncio
async def test_super_admin_allowed_within_open_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Super-admin can mark attendance once the open window has started."""
    super_admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=20
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 200

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_response.status_code == 200
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["membername"] == "testuser"
    assert records[0]["status"] == "present"


@pytest.mark.asyncio
async def test_open_window_uses_effective_start_when_rescheduled_later(
    client: AsyncClient, db_session: AsyncSession
):
    """When the occurrence is rescheduled later, the open window shifts later too.

    Slot key is 20 min from now (would normally be open), but reschedule pushes
    the effective start to +6h. Non-super-admin must be rejected at NOW.
    """
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=20
    )
    new_start = int(
        (datetime.now(timezone.utc) + timedelta(hours=6)).timestamp() * 1000
    )
    await _set_override(db_session, event_id, occurrence_time_utc, new_start)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ATTENDANCE_NOT_YET_OPEN"


@pytest.mark.asyncio
async def test_open_window_uses_effective_start_when_rescheduled_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    """When the occurrence is rescheduled earlier, the open window opens earlier.

    Slot key is 6h from now (would normally be rejected), but reschedule pulls
    the effective start to +20 min. Non-super-admin must be accepted at NOW.
    """
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=6 * 60
    )
    new_start = int(
        (datetime.now(timezone.utc) + timedelta(minutes=20)).timestamp() * 1000
    )
    await _set_override(db_session, event_id, occurrence_time_utc, new_start)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_edit_window_uses_effective_start_when_rescheduled(
    client: AsyncClient, db_session: AsyncSession
):
    """When the occurrence is rescheduled forward into the future, the 15-day
    edit window must be measured from the effective start, not the slot key.

    Slot key is 20 days ago (would close the edit window), but reschedule moves
    the effective start to 1 day ago. Non-super-admin must be accepted.
    """
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=20, db_session=db_session
    )
    new_start = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    await _set_override(db_session, event_id, occurrence_time_utc, new_start)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_edit_window_closed_uses_effective_start_when_rescheduled(
    client: AsyncClient, db_session: AsyncSession
):
    """Reverse of above: slot key is recent but reschedule moves the effective
    start further into the past, closing the edit window earlier."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await create_past_event_and_enroll(
        client, super_admin_token, "testuser", days_ago=5, db_session=db_session
    )
    # Effective start 30 days ago — edit window long closed.
    new_start = int(
        (datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000
    )
    await _set_override(db_session, event_id, occurrence_time_utc, new_start)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"


@pytest.mark.asyncio
async def test_leave_window_uses_effective_start_when_rescheduled_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    """Leave declaration cutoff (start − 2h) must use effective start.

    Slot key is 3h from now (leave still open relative to slot key), but
    reschedule pulls effective start to +1h, which is inside the 2h cutoff.
    Declare leave must be rejected with LEAVE_WINDOW_CLOSED.
    """
    super_admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occurrence_time_utc = await _create_future_event_and_enroll(
        client, super_admin_token, "testuser", minutes_from_now=3 * 60
    )
    new_start = int(
        (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000
    )
    await _set_override(db_session, event_id, occurrence_time_utc, new_start)
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LEAVE_WINDOW_CLOSED"


# ---------------------------------------------------------------------------
# Issue #110: attendance mutations must refuse writes to cancelled occurrences.
# Covers both cancellation paths:
#   - occurrence-level: OccurrenceOverride.status == "cancelled"
#   - event-level: event.until_time set, continued_as_event_id null,
#                  occurrence_time >= event.until_time
# ---------------------------------------------------------------------------


async def _cancel_occurrence_via_override(
    db_session: AsyncSession, event_id: int, occurrence_time_utc: int
) -> None:
    """Install an OccurrenceOverride row with status='cancelled'."""
    from club_server.db.models.occurrence_override import OccurrenceOverride
    from sqlalchemy import select as _sel

    existing = (
        await db_session.execute(
            _sel(OccurrenceOverride).where(
                OccurrenceOverride.event_id == event_id,
                OccurrenceOverride.occurrence_time == occurrence_time_utc,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(
            OccurrenceOverride(
                event_id=event_id,
                occurrence_time=occurrence_time_utc,
                status="cancelled",
            )
        )
    else:
        existing.status = "cancelled"
    await db_session.commit()


async def _cancel_event_series(
    db_session: AsyncSession, event_id: int, until_time_utc: int
) -> None:
    """Cancel the series at ``until_time_utc`` (no continuation)."""
    from club_server.db.models.event import Event
    from sqlalchemy import select as _sel

    event = (
        await db_session.execute(_sel(Event).where(Event.id == event_id))
    ).scalar_one()
    event.until_time = until_time_utc
    event.continued_as_event_id = None
    await db_session.commit()


async def _declare_leave_for_user(
    client: AsyncClient, user_token: str, username: str, event_id: int, occ: int
) -> None:
    resp = await client.post(
        f"/v1/myevents/by_id/{username}/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "Setup"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_mark_attendance_rejects_occurrence_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occ = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )
    await _cancel_occurrence_via_override(db_session, event_id, occ)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_mark_attendance_rejects_event_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occ = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )
    await _cancel_event_series(db_session, event_id, occ)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_declare_leave_rejects_occurrence_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _cancel_occurrence_via_override(db_session, event_id, occ)

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_declare_leave_rejects_event_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _cancel_event_series(db_session, event_id, occ)

    response = await client.post(
        f"/v1/myevents/by_id/testuser/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_approve_leave_rejects_occurrence_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _declare_leave_for_user(client, user_token, "testuser", event_id, occ)
    await _cancel_occurrence_via_override(db_session, event_id, occ)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_approve_leave_rejects_event_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _declare_leave_for_user(client, user_token, "testuser", event_id, occ)
    await _cancel_event_series(db_session, event_id, occ)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/approve",
        json={"membernames": ["testuser"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_reject_leave_rejects_occurrence_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _declare_leave_for_user(client, user_token, "testuser", event_id, occ)
    await _cancel_occurrence_via_override(db_session, event_id, occ)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/reject",
        json={"membernames": ["testuser"], "reason": "n/a"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_reject_leave_rejects_event_level_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _declare_leave_for_user(client, user_token, "testuser", event_id, occ)
    await _cancel_event_series(db_session, event_id, occ)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/reject",
        json={"membernames": ["testuser"], "reason": "n/a"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.asyncio
async def test_attendance_reads_still_work_on_cancelled_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """Read paths must remain unaffected — historical records of cancelled
    occurrences must still be queryable."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session
    )
    event_id, occ = await create_event_and_enroll(client, admin_token, "testuser")
    await _declare_leave_for_user(client, user_token, "testuser", event_id, occ)
    await _cancel_occurrence_via_override(db_session, event_id, occ)

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.status_code == 200
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["status"] == "onLeaveRequested"


# ---------------------------------------------------------------------------
# Issue #251: clear (DELETE) an attendance record back to "not recorded".
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_attendance_removes_record(
    client: AsyncClient, db_session: AsyncSession
):
    """A marked record can be cleared, returning the member to unrecorded."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occ_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    mark = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert mark.status_code == 200

    clear = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert clear.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json() == []


@pytest.mark.asyncio
async def test_clear_attendance_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """Clearing when no record exists returns 404 ATTENDANCE_NOT_FOUND."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occ_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )

    clear = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert clear.status_code == 404
    assert clear.json()["detail"]["code"] == "ATTENDANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_clear_attendance_rejects_leave_record(
    client: AsyncClient, db_session: AsyncSession
):
    """Clearing a leave record is rejected — leave is managed via its own flow."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "testuser", db_session)
    event_id, occ_time = await create_event_and_enroll(
        client, admin_token, "testuser", in_past=True, db_session=db_session
    )
    await _insert_pending_leave(db_session, event_id, occ_time, "testuser")
    # Commit so the failing DELETE (which shares this session and rolls back on
    # error) does not wipe the uncommitted leave record before the GET below.
    await db_session.commit()

    clear = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert clear.status_code == 422
    assert clear.json()["detail"]["code"] == "INVALID_STATE"

    # The leave record is untouched.
    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = get_response.json()
    assert len(records) == 1
    assert records[0]["status"] == "onLeaveRequested"


@pytest.mark.asyncio
async def test_clear_attendance_super_admin_bypasses_edit_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Past the 15-day edit window, only a super-admin can clear a record."""
    from .helpers import create_regular_admin_user

    super_admin_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, super_admin_token, "testuser", db_session
    )
    event_id, occ_time = await create_past_event_and_enroll(
        client, admin_token, "testuser", days_ago=20, db_session=db_session
    )

    # Super-admin can mark beyond the edit window; that gives us a record.
    mark = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        json={"records": [{"membername": "testuser", "status": "present"}]},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert mark.status_code == 200

    # Regular admin is blocked by the closed edit window.
    blocked = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["code"] == "EDIT_WINDOW_CLOSED"

    # Super-admin bypasses it and clears the record.
    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance/testuser",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert cleared.status_code == 204

    get_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occ_time}/attendance",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_response.json() == []
