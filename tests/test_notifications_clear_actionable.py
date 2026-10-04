"""Tests for #102: actionable notifications must be cleared from BOTH
``GET /v1/notifications`` and ``GET /v1/notifications/pending-actions``
once the underlying action is resolved through an API path.

Covers every ``pending_action_type`` registered in
``PENDING_ACTION_JOINS`` with at least one resolution outcome.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user
from .test_notifications import create_user_and_get_token


async def _admin_inbox(client: AsyncClient, token: str) -> list[dict]:
    response = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()["items"]


async def _pending_actions(client: AsyncClient, token: str) -> list[dict]:
    response = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()["items"]


def _ids_of_type(items: list[dict], pending_action_type: str) -> list[int]:
    return [
        item["id"]
        for item in items
        if item.get("pendingActionType") == pending_action_type
    ]


# --- user_approval ---


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_user_approval_cleared_when_admin_approves(
    client: AsyncClient, db_session: AsyncSession
):
    """Approving a pending user must delete the user_approval notification."""
    admin_token = await create_admin_user(db_session)

    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "testpass123",
            "firstName": "Alice",
            "gender": "female",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    login = await client.post(
        "/v1/auth/login",
        json={"username": "alice", "password": "testpass123"},
    )
    alice_token = login.json()["accessToken"]
    await attach_identity_document(db_session, "alice")
    _ = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {alice_token}"},
    )

    # Sanity: admin has the user_approval actionable notification.
    pre_inbox = _ids_of_type(await _admin_inbox(client, admin_token), "user_approval")
    pre_pending = _ids_of_type(
        await _pending_actions(client, admin_token), "user_approval"
    )
    assert len(pre_inbox) >= 1
    assert pre_inbox == pre_pending  # the actionable row is the same

    # Resolve via the API.
    approve = await client.post(
        "/v1/users/by_id/alice/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve.status_code == 200

    # Gone from both feeds.
    assert _ids_of_type(await _admin_inbox(client, admin_token), "user_approval") == []
    assert (
        _ids_of_type(await _pending_actions(client, admin_token), "user_approval") == []
    )


@pytest.mark.requirement("notifications:R98")
@pytest.mark.asyncio
async def test_user_approval_resolution_response_still_emitted(
    client: AsyncClient, db_session: AsyncSession
):
    """Positive control: account.registration_approved must still reach the user."""
    admin_token = await create_admin_user(db_session)
    alice_token = await create_user_and_get_token(
        client,
        admin_token,
        "alice",
        db_session,
        clear_lifecycle_notifications=False,
    )

    inbox = await _admin_inbox(client, alice_token)
    types = [item["type"] for item in inbox]
    assert "account.registration_approved" in types


# --- group_join_request ---


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_group_join_request_cleared_on_approve(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_user_and_get_token(
        client, admin_token, "amy", db_session=db_session
    )

    group_resp = await client.post(
        "/v1/groups",
        json={"name": "U-12"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert group_resp.status_code == 201
    group_id = group_resp.json()["id"]

    join_resp = await client.post(
        f"/v1/mygroups/by_id/amy/join/{group_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert join_resp.status_code == 201
    request_id = join_resp.json()["id"]

    pre_admin = _ids_of_type(
        await _admin_inbox(client, admin_token), "group_join_request"
    )
    assert pre_admin
    assert (
        _ids_of_type(await _pending_actions(client, admin_token), "group_join_request")
        == pre_admin
    )

    approve = await client.post(
        f"/v1/groups/by_id/{group_id}/requests/{request_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve.status_code == 200

    assert (
        _ids_of_type(await _admin_inbox(client, admin_token), "group_join_request")
        == []
    )
    assert (
        _ids_of_type(await _pending_actions(client, admin_token), "group_join_request")
        == []
    )


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_group_join_request_cleared_on_reject(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_user_and_get_token(
        client, admin_token, "amy", db_session=db_session
    )

    group_resp = await client.post(
        "/v1/groups",
        json={"name": "U-12"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    group_id = group_resp.json()["id"]
    join_resp = await client.post(
        f"/v1/mygroups/by_id/amy/join/{group_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    request_id = join_resp.json()["id"]

    reject = await client.post(
        f"/v1/groups/by_id/{group_id}/requests/{request_id}/reject",
        json={"reason": "full"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reject.status_code == 200

    assert (
        _ids_of_type(await _admin_inbox(client, admin_token), "group_join_request")
        == []
    )


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_group_join_request_cleared_on_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_user_and_get_token(
        client, admin_token, "amy", db_session=db_session
    )

    group_resp = await client.post(
        "/v1/groups",
        json={"name": "U-12"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    group_id = group_resp.json()["id"]
    join_resp = await client.post(
        f"/v1/mygroups/by_id/amy/join/{group_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    request_id = join_resp.json()["id"]

    cancel = await client.delete(
        f"/v1/mygroups/by_id/amy/requests/{request_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert cancel.status_code in (200, 204)

    assert (
        _ids_of_type(await _admin_inbox(client, admin_token), "group_join_request")
        == []
    )
    assert (
        _ids_of_type(await _pending_actions(client, admin_token), "group_join_request")
        == []
    )


# --- enrollment_opportunity (invite path) ---


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_enrollment_opportunity_cleared_on_accept(
    client: AsyncClient, db_session: AsyncSession
):
    """Accepting an invitation must clear the enrollment_opportunity notification
    for the invitee from both feeds."""
    from datetime import datetime, timedelta, timezone

    def future_ms(hours: int) -> int:
        return int(
            (datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000
        )

    admin_token = await create_admin_user(db_session)
    member_token = await create_user_and_get_token(
        client, admin_token, "amy", db_session=db_session
    )

    venue_resp = await client.post(
        "/v1/venues",
        json={"name": "Rink"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    venue_id = venue_resp.json()["id"]
    event_resp = await client.post(
        "/v1/events",
        json={
            "title": "Match",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_ms(24),
            "endTimeUtc": future_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_resp.json()["id"]

    invite = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["amy"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert invite.status_code in (200, 201, 204)

    pre_inbox = _ids_of_type(
        await _admin_inbox(client, member_token), "enrollment_opportunity"
    )
    assert pre_inbox
    assert (
        _ids_of_type(
            await _pending_actions(client, member_token), "enrollment_opportunity"
        )
        == pre_inbox
    )

    accept = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/accept",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert accept.status_code in (200, 204)

    assert (
        _ids_of_type(await _admin_inbox(client, member_token), "enrollment_opportunity")
        == []
    )
    assert (
        _ids_of_type(
            await _pending_actions(client, member_token), "enrollment_opportunity"
        )
        == []
    )


# --- enrollment_request ---


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_enrollment_request_cleared_on_approve(
    client: AsyncClient, db_session: AsyncSession
):
    from datetime import datetime, timedelta, timezone

    def future_ms(hours: int) -> int:
        return int(
            (datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000
        )

    admin_token = await create_admin_user(db_session)
    member_token = await create_user_and_get_token(
        client, admin_token, "amy", db_session=db_session
    )

    venue_resp = await client.post(
        "/v1/venues",
        json={"name": "Rink"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    venue_id = venue_resp.json()["id"]
    event_resp = await client.post(
        "/v1/events",
        json={
            "title": "OpenSession",
            "type": "oneOff",
            "venueId": venue_id,
            "visibility": "public",
            "startTimeUtc": future_ms(24),
            "endTimeUtc": future_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_resp.json()["id"]

    request_resp = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/request",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert request_resp.status_code in (200, 201, 204)

    pre = _ids_of_type(await _admin_inbox(client, admin_token), "enrollment_request")
    assert pre

    approve = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": ["amy"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve.status_code in (200, 204)

    assert (
        _ids_of_type(await _admin_inbox(client, admin_token), "enrollment_request")
        == []
    )


# --- attendance_correction ---


@pytest.mark.requirement("notifications:R36")
@pytest.mark.asyncio
async def test_attendance_correction_cleared_on_approve(
    client: AsyncClient, db_session: AsyncSession
):
    """Approving a pending leave request must clear its actionable notification."""
    from datetime import datetime, timedelta, timezone

    def future_ms(hours: int) -> int:
        return int(
            (datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000
        )

    admin_token = await create_admin_user(db_session)
    member_token = await create_user_and_get_token(
        client, admin_token, "amy", db_session=db_session
    )

    venue_resp = await client.post(
        "/v1/venues",
        json={"name": "Rink"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    venue_id = venue_resp.json()["id"]
    start = future_ms(24)
    event_resp = await client.post(
        "/v1/events",
        json={
            "title": "Match",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": future_ms(25),
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    event_id = event_resp.json()["id"]
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    leave_resp = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/occurrences/{start}/leave/request",
        json={"reason": "Medical"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert leave_resp.status_code in (200, 201, 204)

    pre = _ids_of_type(await _admin_inbox(client, admin_token), "attendance_correction")
    assert pre

    approve = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/leave/approve",
        json={"membernames": ["amy"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve.status_code in (200, 204)

    assert (
        _ids_of_type(await _admin_inbox(client, admin_token), "attendance_correction")
        == []
    )
    assert (
        _ids_of_type(
            await _pending_actions(client, admin_token), "attendance_correction"
        )
        == []
    )
