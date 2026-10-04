"""End-to-end workflow integration tests.

These tests exercise multi-step scenarios that span multiple API domains,
adapted from SDK workflow test patterns.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_regular_admin_user,
)


async def create_user_and_get_token(
    client: AsyncClient, admin_token: str, username: str, db_session: AsyncSession
) -> str:
    """Helper to create a regular user and return their access token."""
    await client.post(
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
    await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {_pre.json()['accessToken']}"},
    )
    await client.post(
        f"/v1/users/by_id/{username}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login_response = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    return login_response.json()["accessToken"]


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


def future_time_ms(hours: int = 24) -> int:
    """Helper to get a future time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


def past_time_ms(hours: int = 24) -> int:
    """Helper to get a past time as milliseconds since epoch."""
    return int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)


# =============================================================================
# Workflow 1: Enrollment → Attendance story
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_enrollment_attendance_story(
    client: AsyncClient, db_session: AsyncSession
):
    """Full story: create event → invite → accept/decline → mark attendance → stats."""
    admin_token = await create_admin_user(db_session)
    alice_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    bob_token = await create_user_and_get_token(client, admin_token, "bob", db_session)

    from sqlalchemy import select
    from club_server.db.models.event import Event
    from club_server.db.models.enrollment import Enrollment

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Weekly Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    # Invite both
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice", "bob"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Alice accepts, Bob declines
    await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/bob/{event_id}/enrollments/decline",
        headers={"Authorization": f"Bearer {bob_token}"},
    )

    # Verify enrollment states
    enrollments_response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    enrollments = enrollments_response.json()["enrollments"]
    assert enrollments["alice"] == "accepted"
    assert enrollments["bob"] == "declined"

    # Move the occurrence into the recent past so attendance marking is
    # permitted (super-admin no longer bypasses check_open_window — #106).
    past_start = past_time_ms(1)
    event_row = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event_row.start_time = past_start
    event_row.end_time = past_start + 60 * 60 * 1000
    alice_enrollment = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == "alice",
            )
        )
    ).scalar_one()
    alice_enrollment.enrolled_at = past_start - 1
    await db_session.commit()
    start_time = past_start

    # Mark attendance for alice
    await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Verify attendance records
    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)
    attendance_response = await client.get(
        f"/v1/myevents/by_id/alice/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert attendance_response.status_code == 200
    records = attendance_response.json()
    assert len(records) >= 1
    assert any(r["status"] == "present" for r in records)


# =============================================================================
# Workflow 2: Cancellation flow
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_cancellation_flow(
    client: AsyncClient, db_session: AsyncSession
):
    """Create → assign → cancel event → verify enrollment survives the cancel."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "member", db_session)

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "To Be Cancelled",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    # Assign and accept
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["member"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Terminate the programme at its only occurrence
    cancel_response = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={"reason": "Instructor unavailable", "cutoffTimeUtc": start_time},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert cancel_response.status_code == 200

    # #108: cancellation no longer mutates enrollment rows — the enrollment
    # survives the cancel (it records that the user signed up).
    enrollment_response = await client.get(
        f"/v1/myevents/by_id/member/{event_id}/enrollments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert enrollment_response.json()["status"] == "assigned"


# =============================================================================
# Workflow 3: Programme lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_programme_lifecycle(
    client: AsyncClient, db_session: AsyncSession
):
    """Create programme → assign → reschedule occurrence → mark attendance."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "player", db_session)

    venue_id = await create_venue(client, admin_token)
    # Whole seconds, so the start is one of the camp's own slots (#470).
    start_time = future_time_ms(24) // 1000 * 1000

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Daily Programme",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    # Assign player
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["player"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Install an override pulling the effective start into the recent past
    # so attendance marking is permitted (super-admin no longer bypasses
    # check_open_window — issue #106). The pre-fix test went through the
    # reschedule API here, but its return value was never asserted and the
    # subsequent mark_attendance succeeded only via the now-removed bypass.
    from sqlalchemy import select
    from club_server.db.models.occurrence_override import OccurrenceOverride
    from club_server.db.models.enrollment import Enrollment

    past_start = past_time_ms(1)
    db_session.add(
        OccurrenceOverride(
            event_id=event_id,
            occurrence_time=start_time,
            status="rescheduled",
            new_start_time=past_start,
        )
    )
    enrollment_row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id,
                Enrollment.membername == "player",
            )
        )
    ).scalar_one()
    enrollment_row.enrolled_at = past_start - 1
    await db_session.commit()

    # Mark attendance on the rescheduled occurrence
    att_response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        json={"records": [{"membername": "player", "status": "present"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert att_response.status_code == 200

    # Verify attendance recorded
    get_att = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_att.json()[0]["status"] == "present"


# =============================================================================
# Workflow 4: Camp enrollment (public event, request to join)
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_camp_enrollment(client: AsyncClient, db_session: AsyncSession):
    """Create public camp → users request to join → approve some, reject others."""
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "camper1", db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "camper2", db_session
    )

    venue_id = await create_venue(client, admin_token)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Summer Camp",
            "type": "camp",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(72),
            "endTimeUtc": future_time_ms(74),
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    # Users request to join
    await client.post(
        f"/v1/myevents/by_id/camper1/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    await client.post(
        f"/v1/myevents/by_id/camper2/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {user2_token}"},
    )

    # Approve camper1, reject camper2
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["camper1"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["camper2"], "reason": "Age requirement not met"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    enrollments = (
        await client.get(
            f"/v1/events/by_id/{event_id}/enrollments",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()["enrollments"]
    assert enrollments["camper1"] == "accepted"
    assert enrollments["camper2"] == "rejected"


# =============================================================================
# Workflow 6: Cancelled event blocks enrollment actions
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_cancelled_event_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """Cancel event → verify new enrollment actions are rejected for non-super-admin."""
    admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_user_and_get_token(client, admin_token, "latecomer", db_session)

    venue_id = await create_venue(client, admin_token)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Cancelled Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    # Terminate at the only occurrence, so no live occurrence remains
    ended = await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={
            "reason": "No longer needed",
            "cutoffTimeUtc": event_response.json()["startTimeUtc"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ended.status_code == 200, ended.text

    # Try to invite — should fail
    invite_response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["latecomer"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert invite_response.status_code == 422
    assert invite_response.json()["detail"]["code"] == "INVALID_STATE"

    # Try to assign — should fail
    assign_response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["latecomer"]},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert assign_response.status_code == 422
    assert assign_response.json()["detail"]["code"] == "INVALID_STATE"


# =============================================================================
# Workflow 7: Calendar range query
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_calendar_range_query(
    client: AsyncClient, db_session: AsyncSession
):
    """Create recurring events → query by date range → verify occurrences filtered."""
    admin_token = await create_admin_user(db_session)
    venue_id = await create_venue(client, admin_token)

    # Create a daily programme starting tomorrow (5 occurrences)
    near_start = future_time_ms(24)
    await client.post(
        "/v1/events",
        json={
            "title": "Daily Programme",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": near_start,
            "endTimeUtc": future_time_ms(25),
            "rrule": "FREQ=DAILY;COUNT=5",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Query narrow range (0-36 hours) — should get only 1 occurrence
    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": future_time_ms(0), "toTimeUtc": future_time_ms(36)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["eventTitle"] == "Daily Programme"

    # Query wider range (0-120 hours) — should get all 5 occurrences
    response2 = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": future_time_ms(0), "toTimeUtc": future_time_ms(130)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data2 = response2.json()
    assert len(data2) == 5

    # Query with type filter
    response3 = await client.get(
        "/v1/events/occurrences",
        params={
            "fromTimeUtc": future_time_ms(0),
            "toTimeUtc": future_time_ms(130),
            "type": "oneOff",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # No oneOff events, so empty
    assert len(response3.json()) == 0


@pytest.mark.asyncio
async def test_workflow_oneoff_event_excluded_from_out_of_range_query(
    client: AsyncClient, db_session: AsyncSession
):
    """A one-off event outside the queried range should not appear in results.

    See issue #126
    """
    admin_token = await create_admin_user(db_session)
    venue_id = await create_venue(client, admin_token)

    # Create a one-off event 10 days in the future
    far_start = future_time_ms(240)
    await client.post(
        "/v1/events",
        json={
            "title": "Far Future Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": far_start,
            "endTimeUtc": future_time_ms(241),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Query a narrow range (next 48 hours) — the event at 240h should NOT appear
    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": future_time_ms(0), "toTimeUtc": future_time_ms(48)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    titles = [o["eventTitle"] for o in response.json()]
    assert "Far Future Event" not in titles


# =============================================================================
# Workflow 8: Past event edge cases
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_past_event_edge_cases(
    client: AsyncClient, db_session: AsyncSession
):
    """Time-based restrictions: leave window closed for imminent events."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "player", db_session
    )

    venue_id = await create_venue(client, admin_token)

    # Create event starting in 1 hour (within leave window)
    soon_time = int(
        (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000
    )
    end_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp() * 1000)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Imminent Event",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": soon_time,
            "endTimeUtc": end_time,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["player"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Attempt to declare leave — should be blocked (within 2-hour window)
    leave_response = await client.post(
        f"/v1/myevents/by_id/player/{event_id}/occurrences/{soon_time}/leave/request",
        json={"reason": "Running late"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert leave_response.status_code == 422
    assert leave_response.json()["detail"]["code"] == "LEAVE_WINDOW_CLOSED"


# =============================================================================
# Workflow 9: Event split enrollment propagation
# =============================================================================


# =============================================================================
# Workflow 10: Withdrawal lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_withdrawal_lifecycle(
    client: AsyncClient, db_session: AsyncSession
):
    """Assign → withdraw → reject → re-withdraw → approve."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "member", db_session
    )
    event_id_response = await client.post(
        "/v1/events",
        json={
            "title": "Withdrawal Test",
            "type": "programme",
            "venueId": await create_venue(client, admin_token),
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_id_response.json()["id"]

    # Assign
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["member"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # First withdrawal request
    await client.post(
        f"/v1/myevents/by_id/member/{event_id}/enrollments/withdraw",
        json={"reason": "Want to leave"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    # Reject the withdrawal
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["member"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Verify back to assigned
    status1 = (
        await client.get(
            f"/v1/myevents/by_id/member/{event_id}/enrollments",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()["status"]
    assert status1 == "assigned"

    # Second withdrawal request
    await client.post(
        f"/v1/myevents/by_id/member/{event_id}/enrollments/withdraw",
        json={"reason": "Still want to leave"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    # Approve this time
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["member"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Verify withdrawn
    status2 = (
        await client.get(
            f"/v1/myevents/by_id/member/{event_id}/enrollments",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()["status"]
    assert status2 == "withdrawn"


# =============================================================================
# Workflow 11: Leave and attendance
# =============================================================================


@pytest.mark.asyncio
async def test_workflow_leave_and_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    """Enroll → declare leave → approve → verify in stats."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "player", db_session
    )

    venue_id = await create_venue(client, admin_token)
    start_time = future_time_ms(24)

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Training",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_time,
            "endTimeUtc": future_time_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_response.json()["id"]

    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["player"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Declare leave
    await client.post(
        f"/v1/myevents/by_id/player/{event_id}/occurrences/{start_time}/leave/request",
        json={"reason": "Family emergency"},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    # Approve leave
    await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/leave/approve",
        json={"membernames": ["player"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Verify attendance record shows onLeave
    att_response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start_time}/attendance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = att_response.json()
    assert records[0]["status"] == "onLeave"

    # Check attendance records reflect the leave
    from_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    to_time = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)
    attendance_response = await client.get(
        f"/v1/myevents/by_id/player/attendance?fromTimeUtc={from_time}&toTimeUtc={to_time}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert attendance_response.status_code == 200
    assert any(r["status"] == "onLeave" for r in attendance_response.json())


# =============================================================================
# Workflow 12: User block cascades
# =============================================================================


@pytest.mark.requirement("auth:R12")
@pytest.mark.asyncio
async def test_workflow_user_block_cascades(
    client: AsyncClient, db_session: AsyncSession
):
    """Block user → token rejected → unblock → access restored."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "baduser", db_session
    )

    # Verify user can access their data
    me_response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert me_response.status_code == 200

    # Block the user
    block_response = await client.post(
        "/v1/users/by_id/baduser/block",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert block_response.status_code == 200
    assert block_response.json()["status"] == "blocked"

    # User's token should now be rejected
    blocked_response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert blocked_response.status_code == 401
    assert blocked_response.json()["detail"]["code"] == "ACCOUNT_BLOCKED"

    # Unblock the user
    unblock_response = await client.post(
        "/v1/users/by_id/baduser/unblock",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unblock_response.status_code == 200
    assert unblock_response.json()["status"] == "active"

    # User can access again
    restored_response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert restored_response.status_code == 200
    assert restored_response.json()["username"] == "baduser"
