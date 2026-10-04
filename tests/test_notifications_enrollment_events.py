"""Tests for #48 — enrollment-domain notifications.

Wired events (issue table cross-referenced with the actual code surface):

| Event                       | Recipient         | Category      |
|-----------------------------|-------------------|---------------|
| enrollment.opened           | invitee           | actionable    |
| enrollment.rsvp(requested)  | staff (admins+coaches) | actionable    |
| enrollment.rsvp(accepted|declined) | staff (admins+coaches) | informational |
| enrollment.admin_enrolled   | enrolled user     | informational |
| enrollment.cancelled_self   | staff             | informational |
| enrollment.cancelled_admin  | user              | informational |
| enrollment.closed           | user              | informational |

Events from the issue table that this codebase does **not** support and are
therefore not wired here: enrollment.waitlisted / waitlist_promoted (no
waitlist concept), enrollment.deadline_reminder (needs scheduler — deferred
to follow-up issue), enrollment.effective_start_changed (belongs to event
domain, will be addressed in #50).
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
    create_coach_user,
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


async def _create_event(
    client: AsyncClient, admin_token: str, *, event_type: str = "programme"
) -> int:
    venue = await client.post(
        "/v1/venues", json={"name": "V"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]
    response = await client.post(
        "/v1/events",
        json={
            "title": "E",
            "type": event_type,
            "venueId": venue_id,
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers=auth(admin_token),
    )
    return response.json()["id"]


# ---------------------------------------------------------------------------
# enrollment.opened — actionable, invitee recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R31")
@pytest.mark.requirement("notifications:R58")
@pytest.mark.asyncio
async def test_invite_emits_enrollment_opened_actionable(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    invite = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    assert invite.status_code == 204

    rows = await _notifications_for(db_session, "alice", "enrollment.opened")
    assert len(rows) == 1
    n = rows[0]
    assert n.pending_action_type == "enrollment_opportunity"
    assert n.pending_action_id is not None
    assert n.payload["data"]["eventId"] == event_id

    # Visible in pending-actions for the invitee.
    pa = await client.get("/v1/notifications/pending-actions", headers=auth(user_token))
    assert pa.json()["total"] == 1


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_enrollment_opportunity_auto_dismisses_on_accept(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    # Sanity check.
    pa_before = await client.get(
        "/v1/notifications/pending-actions", headers=auth(user_token)
    )
    assert pa_before.json()["total"] == 1

    accept = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers=auth(user_token),
    )
    assert accept.status_code == 204

    pa_after = await client.get(
        "/v1/notifications/pending-actions", headers=auth(user_token)
    )
    assert pa_after.json()["total"] == 0

    # And the opened-notification is also removed from the regular feed (#102).
    feed = await client.get("/v1/notifications", headers=auth(user_token))
    assert not any(item["type"] == "enrollment.opened" for item in feed.json()["items"])


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_enrollment_opportunity_auto_dismisses_on_decline(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/decline",
        headers=auth(user_token),
    )

    pa = await client.get("/v1/notifications/pending-actions", headers=auth(user_token))
    assert pa.json()["total"] == 0


# ---------------------------------------------------------------------------
# enrollment.rsvp — informational, staff recipients
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R57")
@pytest.mark.requirement("notifications:R59")
@pytest.mark.asyncio
async def test_rsvp_accepted_notifies_staff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach1")
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers=auth(user_token),
    )

    # admin + coach1 (staff) both receive the rsvp event.
    for staff_name in ("admin", "coach1"):
        rows = await _notifications_for(db_session, staff_name, "enrollment.rsvp")
        assert len(rows) == 1, f"{staff_name} should receive rsvp event"
        data = rows[0].payload["data"]
        assert data["outcome"] == "accepted"
        assert data["memberUsername"] == "alice"
        assert data["eventId"] == event_id

    # The user themselves does NOT receive an rsvp event (only staff do).
    user_rows = await _notifications_for(db_session, "alice", "enrollment.rsvp")
    assert user_rows == []

    # Sanity: coach can see it on their feed.
    feed = await client.get("/v1/notifications", headers=auth(coach_token))
    assert any(item["type"] == "enrollment.rsvp" for item in feed.json()["items"])


@pytest.mark.requirement("notifications:R59")
@pytest.mark.asyncio
async def test_rsvp_declined_notifies_staff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/decline",
        headers=auth(user_token),
    )

    rows = await _notifications_for(db_session, "admin", "enrollment.rsvp")
    assert len(rows) == 1
    assert rows[0].payload["data"]["outcome"] == "declined"


@pytest.mark.requirement("notifications:R31")
@pytest.mark.requirement("notifications:R57")
@pytest.mark.requirement("notifications:R60")
@pytest.mark.asyncio
async def test_rsvp_requested_notifies_staff_actionable(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach1")
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/request",
        headers=auth(user_token),
    )

    for staff_name in ("admin", "coach1"):
        rows = await _notifications_for(db_session, staff_name, "enrollment.rsvp")
        assert len(rows) == 1, f"{staff_name} should receive rsvp event"
        n = rows[0]
        assert n.payload["data"]["outcome"] == "requested"
        assert n.pending_action_type == "enrollment_request"
        assert n.pending_action_id is not None

    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(coach_token)
    )
    assert pa.json()["total"] == 1


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_enrollment_request_auto_dismisses_on_approve(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/request",
        headers=auth(user_token),
    )
    pa_before = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa_before.json()["total"] == 1

    approve = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    assert approve.status_code == 204

    pa_after = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa_after.json()["total"] == 0

    # The actionable enrollment.rsvp request notification is removed from the
    # regular feed too (#102) — only its outcome=requested rows are deleted.
    feed = await client.get("/v1/notifications", headers=auth(admin_token))
    assert not any(
        item["type"] == "enrollment.rsvp"
        and item["payload"]["data"]["outcome"] == "requested"
        for item in feed.json()["items"]
    )


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_enrollment_request_auto_dismisses_on_reject(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/request",
        headers=auth(user_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["alice"], "reason": "full"},
        headers=auth(admin_token),
    )

    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa.json()["total"] == 0


@pytest.mark.requirement("notifications:R59")
@pytest.mark.asyncio
async def test_rsvp_accepted_is_not_actionable(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers=auth(user_token),
    )

    rows = await _notifications_for(db_session, "admin", "enrollment.rsvp")
    assert len(rows) == 1
    assert rows[0].pending_action_type is None
    assert rows[0].pending_action_id is None


@pytest.mark.requirement("notifications:R59")
@pytest.mark.asyncio
async def test_rsvp_declined_is_not_actionable(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/decline",
        headers=auth(user_token),
    )

    rows = await _notifications_for(db_session, "admin", "enrollment.rsvp")
    assert len(rows) == 1
    assert rows[0].pending_action_type is None
    assert rows[0].pending_action_id is None


# ---------------------------------------------------------------------------
# enrollment.admin_enrolled — informational, enrolled user recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R61")
@pytest.mark.asyncio
async def test_assign_emits_admin_enrolled(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "enrollment.admin_enrolled")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["eventId"] == event_id
    assert data["trial"] is False


@pytest.mark.requirement("notifications:R61")
@pytest.mark.asyncio
async def test_assign_trial_emits_admin_enrolled_with_trial_flag(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)  # programme

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "alice"},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "enrollment.admin_enrolled")
    assert len(rows) == 1
    assert rows[0].payload["data"]["trial"] is True


@pytest.mark.requirement("notifications:R62")
@pytest.mark.asyncio
async def test_approve_request_emits_admin_enrolled_via_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/request",
        headers=auth(user_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "enrollment.admin_enrolled")
    assert len(rows) == 1
    assert rows[0].payload["data"]["viaRequest"] is True


# ---------------------------------------------------------------------------
# enrollment.closed — informational, user recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R63")
@pytest.mark.asyncio
async def test_reject_request_emits_enrollment_closed(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/request",
        headers=auth(user_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["alice"], "reason": "full"},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "enrollment.closed")
    assert len(rows) == 1
    assert rows[0].payload["data"]["reason"] == "full"


# ---------------------------------------------------------------------------
# enrollment.cancelled_admin — informational, user recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R65")
@pytest.mark.asyncio
async def test_remove_enrollment_emits_cancelled_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "enrollment.cancelled_admin")
    assert len(rows) == 1
    assert rows[0].payload["data"]["eventId"] == event_id


@pytest.mark.requirement("notifications:R66")
@pytest.mark.asyncio
async def test_approve_withdrawal_emits_cancelled_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "schedule"},
        headers=auth(user_token),
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "alice", "enrollment.cancelled_admin")
    assert len(rows) == 1
    assert rows[0].payload["data"]["viaWithdrawal"] is True


# ---------------------------------------------------------------------------
# enrollment.cancelled_self — informational, staff recipients
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R64")
@pytest.mark.asyncio
async def test_request_withdrawal_emits_cancelled_self(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")
    user_token = await _register_member(client, admin_token, "alice", db_session)
    event_id = await _create_event(client, admin_token)

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "conflict"},
        headers=auth(user_token),
    )

    for staff_name in ("admin", "ra"):
        rows = await _notifications_for(
            db_session, staff_name, "enrollment.cancelled_self"
        )
        assert len(rows) == 1
        data = rows[0].payload["data"]
        assert data["memberUsername"] == "alice"
        assert data["reason"] == "conflict"

    # The user themselves does not receive it.
    user_rows = await _notifications_for(
        db_session, "alice", "enrollment.cancelled_self"
    )
    assert user_rows == []
