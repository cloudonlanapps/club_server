"""Tests for #49 — attendance-domain notifications.

Wired events:

| Event                          | Recipient | Category      |
|--------------------------------|-----------|---------------|
| attendance.marked              | user      | informational |
| attendance.correction_requested| staff     | actionable    |
| attendance.correction_response | user      | informational |

Events from the issue table not wired here:
- attendance.pending_mark — coach-facing reminder fired after an occurrence
  ends without a mark; needs the scheduler (deferred follow-up issue).
- attendance.absence_warning — derived from a streak of absences;
  needs aggregation logic outside the scope of this PR.
- attendance.check_in_window / attendance.no_check_in — no self-check-in
  flow exists in the API today.

The pending-action carrier for `correction_requested` is the
``AttendanceRecord`` row in status ``onLeaveRequested``. Approve/reject
flips the status, which auto-dismisses the actionable notification from
the staff pending-actions feed.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_regular_admin_user,
)


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


async def _move_event_to_recent_past(
    db_session: AsyncSession, event_id: int, username: str
) -> int:
    """Move event to ~1h ago. Required for attendance marking after #106:
    super-admin no longer bypasses check_open_window."""
    from club_server.db.models.event import Event
    from club_server.db.models.enrollment import Enrollment

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
                Enrollment.membername == username,
            )
        )
    ).scalar_one()
    enrollment_row.enrolled_at = past_start - 1
    await db_session.commit()
    return past_start


async def _setup_enrolled_user(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    username: str = "alice",
) -> tuple[str, int, int]:
    """Returns (user_token, event_id, occurrence_time_utc)."""
    user_token = await _register_member(client, admin_token, username, db_session)
    venue = await client.post(
        "/v1/venues", json={"name": "V"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]
    start = future_time_ms(24)
    end = future_time_ms(25)
    event = await client.post(
        "/v1/events",
        json={
            "title": "E",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": end,
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    return user_token, event_id, start


# ---------------------------------------------------------------------------
# attendance.marked
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R88")
@pytest.mark.asyncio
async def test_mark_attendance_emits_attendance_marked(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _, event_id, _ = await _setup_enrolled_user(client, admin_token, db_session)
    occ = await _move_event_to_recent_past(db_session, event_id, "alice")

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "alice", "attendance.marked")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["occurrenceTimeUtc"] == occ
    assert data["status"] == "present"


@pytest.mark.requirement("notifications:R89")
@pytest.mark.asyncio
async def test_remark_attendance_emits_another_notification(
    client: AsyncClient, db_session: AsyncSession
):
    """Status correction (present → absent) is a fresh notification."""
    admin_token = await create_admin_user(db_session)
    _, event_id, _ = await _setup_enrolled_user(client, admin_token, db_session)
    occ = await _move_event_to_recent_past(db_session, event_id, "alice")

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/attendance",
        json={"records": [{"membername": "alice", "status": "absent"}]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "attendance.marked")
    assert len(rows) == 2
    statuses = [r.payload["data"]["status"] for r in rows]
    assert "present" in statuses and "absent" in statuses


# ---------------------------------------------------------------------------
# attendance.correction_requested (actionable)
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R31")
@pytest.mark.requirement("notifications:R90")
@pytest.mark.asyncio
async def test_declare_leave_emits_correction_requested_to_staff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session, "ra")
    user_token, event_id, occ = await _setup_enrolled_user(
        client, admin_token, db_session
    )

    declare = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "doctor"},
        headers=auth(user_token),
    )
    assert declare.status_code == 204

    for staff_name in ("admin", "ra"):
        rows = await _notifications_for(
            db_session, staff_name, "attendance.correction_requested"
        )
        assert len(rows) == 1, f"{staff_name} should receive correction_requested"
        n = rows[0]
        assert n.pending_action_type == "attendance_correction"
        assert n.pending_action_id is not None
        data = n.payload["data"]
        assert data["memberUsername"] == "alice"
        assert data["reason"] == "doctor"

    # Staff sees it in pending-actions.
    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(regular_admin_token)
    )
    assert pa.json()["total"] == 1


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_correction_request_auto_dismisses_on_approve(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token, event_id, occ = await _setup_enrolled_user(
        client, admin_token, db_session
    )

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "doctor"},
        headers=auth(user_token),
    )

    pa_before = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa_before.json()["total"] == 1

    approve = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/approve",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    assert approve.status_code == 204

    pa_after = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa_after.json()["total"] == 0


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_correction_request_auto_dismisses_on_reject(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token, event_id, occ = await _setup_enrolled_user(
        client, admin_token, db_session
    )

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "doctor"},
        headers=auth(user_token),
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/reject",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa.json()["total"] == 0


# ---------------------------------------------------------------------------
# attendance.correction_response
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R91")
@pytest.mark.asyncio
async def test_approve_leave_emits_correction_response_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token, event_id, occ = await _setup_enrolled_user(
        client, admin_token, db_session
    )

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "doctor"},
        headers=auth(user_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/approve",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(
        db_session, "alice", "attendance.correction_response"
    )
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["outcome"] == "approved"
    assert data["eventId"] == event_id


@pytest.mark.requirement("notifications:R91")
@pytest.mark.asyncio
async def test_reject_leave_emits_correction_response_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token, event_id, occ = await _setup_enrolled_user(
        client, admin_token, db_session
    )

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{occ}/leave/request",
        json={"reason": "doctor"},
        headers=auth(user_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/leave/reject",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(
        db_session, "alice", "attendance.correction_response"
    )
    assert len(rows) == 1
    assert rows[0].payload["data"]["outcome"] == "rejected"
