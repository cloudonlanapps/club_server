import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification
from club_server.services.notification import (
    NotificationEvent,
    NotificationService,
    PAYLOAD_VERSION,
)
from club_server.utils import now_utc_ms
from .helpers import attach_identity_document, create_admin_user


def make_payload(event_type: str = "legacy", **data: object) -> dict[str, object]:
    return {"v": PAYLOAD_VERSION, "type": event_type, "data": dict(data)}


async def create_user_and_get_token(
    client: AsyncClient,
    admin_token: str,
    username: str,
    db_session: AsyncSession | None = None,
    *,
    clear_lifecycle_notifications: bool = True,
) -> str:
    """Helper to create a regular user and return their access token.

    Registering and approving the user fires `user.registration_pending`
    (to admins) and `account.registration_approved` (to the new user) as a side effect.
    By default those rows are cleared so the tests in this file — which
    exercise the *generic* notification feed — can keep asserting against
    clean state. Tests that specifically verify the lifecycle notification
    flow (e.g. ``test_notifications_clear_actionable``) pass
    ``clear_lifecycle_notifications=False``.
    """
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
    assert db_session is not None, "create_user_and_get_token requires db_session"
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

    if db_session is not None and clear_lifecycle_notifications:
        await db_session.execute(
            delete(Notification).where(
                Notification.type.in_(
                    [
                        "user.registration_pending",
                        "account.registration_approved",
                    ]
                )
            )
        )
        await db_session.commit()

    return login_response.json()["accessToken"]


async def create_notification(
    db_session: AsyncSession,
    username: str,
    event_type: str = "legacy",
    pending_action_type: str | None = None,
    pending_action_id: int | None = None,
    **data: object,
) -> Notification:
    """Helper to create a notification directly in DB."""
    notification = Notification(
        username=username,
        type=event_type,
        channel="app",
        payload=make_payload(event_type, **data),
        pending_action_type=pending_action_type,
        pending_action_id=pending_action_id,
        created_at=now_utc_ms(),
    )
    db_session.add(notification)
    await db_session.commit()
    await db_session.refresh(notification)
    return notification


@pytest.mark.requirement("notifications:R10")
@pytest.mark.asyncio
async def test_list_notifications_empty(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    response = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.requirement("notifications:R10")
@pytest.mark.asyncio
async def test_list_notifications(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    _ = await create_notification(db_session, "testuser", "group.invite", groupId=1)
    _ = await create_notification(db_session, "testuser", "group.invite", groupId=2)

    response = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert response.json()["total"] == 2


@pytest.mark.requirement("notifications:R12")
@pytest.mark.asyncio
async def test_list_unread_only(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    n1 = await create_notification(db_session, "testuser", "group.invite", groupId=1)
    n2 = await create_notification(db_session, "testuser", "group.invite", groupId=2)
    n1_id, n2_id = n1.id, n2.id

    _ = await client.post(
        f"/v1/notifications/by_id/{n2_id}/read",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    response = await client.get(
        "/v1/notifications?unreadOnly=true",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == n1_id


@pytest.mark.requirement("notifications:R16")
@pytest.mark.asyncio
async def test_user_only_sees_own_notifications(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user1_token = await create_user_and_get_token(
        client, admin_token, "user1", db_session=db_session
    )
    _ = await create_user_and_get_token(
        client, admin_token, "user2", db_session=db_session
    )

    _ = await create_notification(db_session, "user1", "group.invite", groupId=1)
    _ = await create_notification(db_session, "user2", "group.invite", groupId=2)

    response = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["username"] == "user1"
    assert items[0]["payload"]["data"]["groupId"] == 1


@pytest.mark.requirement("notifications:R14")
@pytest.mark.asyncio
async def test_mark_notification_read(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    notification = await create_notification(
        db_session, "testuser", "group.invite", groupId=1
    )

    response = await client.post(
        f"/v1/notifications/by_id/{notification.id}/read",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    list_response = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert list_response.json()["items"][0]["isRead"] is True


@pytest.mark.requirement("notifications:R17")
@pytest.mark.asyncio
async def test_cannot_mark_other_user_notification(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, admin_token, "user1", db_session=db_session
    )
    user2_token = await create_user_and_get_token(
        client, admin_token, "user2", db_session=db_session
    )

    notification = await create_notification(
        db_session, "user1", "group.invite", groupId=1
    )

    response = await client.post(
        f"/v1/notifications/by_id/{notification.id}/read",
        headers={"Authorization": f"Bearer {user2_token}"},
    )
    assert response.status_code == 404


@pytest.mark.requirement("notifications:R15")
@pytest.mark.asyncio
async def test_mark_all_read(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    _ = await create_notification(db_session, "testuser", "group.invite", groupId=1)
    _ = await create_notification(db_session, "testuser", "group.invite", groupId=2)

    response = await client.post(
        "/v1/notifications/read-all",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 204

    list_response = await client.get(
        "/v1/notifications?unreadOnly=true",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert len(list_response.json()["items"]) == 0


@pytest.mark.requirement("notifications:R13")
@pytest.mark.asyncio
async def test_get_unread_count(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    _ = await create_notification(db_session, "testuser", "group.invite", groupId=1)
    _ = await create_notification(db_session, "testuser", "group.invite", groupId=2)

    response = await client.get(
        "/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 2


@pytest.mark.requirement("notifications:R21")
@pytest.mark.asyncio
async def test_get_preferences(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    response = await client.get(
        "/v1/notifications/preferences",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    prefs = response.json()
    assert prefs["emailEnabled"] is True
    assert prefs["pushEnabled"] is True
    assert prefs["smsEnabled"] is False


@pytest.mark.requirement("notifications:R22")
@pytest.mark.asyncio
async def test_update_preference(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    response = await client.patch(
        "/v1/notifications/preferences",
        json={"emailEnabled": False},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["emailEnabled"] is False

    get_response = await client.get(
        "/v1/notifications/preferences",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert get_response.json()["emailEnabled"] is False


@pytest.mark.requirement("notifications:R23")
@pytest.mark.asyncio
async def test_update_preference_invalid_value(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "testuser", db_session=db_session
    )

    response = await client.patch(
        "/v1/notifications/preferences",
        json={"emailEnabled": "not_a_bool"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422


@pytest.mark.requirement("notifications:R18")
@pytest.mark.asyncio
async def test_unauthenticated_access_fails(client: AsyncClient):
    response = await client.get("/v1/notifications")
    assert response.status_code == 401

    response = await client.get("/v1/notifications/preferences")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# New tests for #45: payload round-trip + pending-action link columns + dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R25")
@pytest.mark.asyncio
async def test_admin_create_with_payload_and_pending_action(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin POST round-trips payload and pending-action link columns."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, admin_token, "alice", db_session=db_session
    )

    body = {
        "username": "alice",
        "type": "group.invite",
        "channel": "app",
        "payload": {
            "v": 1,
            "type": "group.invite",
            "data": {"groupId": 42, "groupName": "U-12", "actorUsername": "admin"},
        },
        "pendingActionType": "group_invitation",
        "pendingActionId": 7,
    }
    response = await client.post(
        "/v1/notifications",
        json=body,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["type"] == "group.invite"
    assert created["payload"]["data"]["groupId"] == 42
    assert created["pendingActionType"] == "group_invitation"
    assert created["pendingActionId"] == 7
    assert created["broadcastId"] is None


@pytest.mark.requirement("notifications:R1")
@pytest.mark.asyncio
async def test_list_surfaces_payload_and_pending_action(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /notifications returns payload and pending-action fields."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session=db_session
    )

    _ = await create_notification(
        db_session,
        "alice",
        event_type="group.join_request",
        pending_action_type="group_join_request",
        pending_action_id=99,
        groupId=3,
        requesterUsername="bob",
    )

    response = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "group.join_request"
    assert item["payload"]["v"] == 1
    assert item["payload"]["type"] == "group.join_request"
    assert item["payload"]["data"]["requesterUsername"] == "bob"
    assert item["pendingActionType"] == "group_join_request"
    assert item["pendingActionId"] == 99


@pytest.mark.requirement("notifications:R27")
@pytest.mark.asyncio
async def test_admin_create_rejects_unknown_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/notifications",
        json={
            "username": "ghost",
            "type": "group.invite",
            "channel": "app",
            "payload": {"v": 1, "type": "group.invite", "data": {}},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("notifications:R1")
@pytest.mark.requirement("notifications:R2")
@pytest.mark.requirement("notifications:R5")
@pytest.mark.asyncio
async def test_notify_for_event_fans_out_to_recipients(
    client: AsyncClient, db_session: AsyncSession
):
    """notify_for_event inserts one row per valid recipient with the wrapped payload."""
    admin_token = await create_admin_user(db_session)
    _ = await create_user_and_get_token(
        client, admin_token, "u1", db_session=db_session
    )
    _ = await create_user_and_get_token(
        client, admin_token, "u2", db_session=db_session
    )

    svc = NotificationService(db_session)
    created = await svc.notify_for_event(
        NotificationEvent(
            type="group.invite",
            recipients=["u1", "u2", "ghost"],
            data={"groupId": 5, "groupName": "U-14", "actorUsername": "admin"},
            pending_action_type="group_invitation",
            pending_action_id=11,
        )
    )

    # Only u1 and u2 exist; ghost is silently skipped.
    assert len(created) == 2
    usernames = {n.username for n in created}
    assert usernames == {"u1", "u2"}
    for n in created:
        assert n.type == "group.invite"
        assert n.payload == {
            "v": 1,
            "type": "group.invite",
            "data": {"groupId": 5, "groupName": "U-14", "actorUsername": "admin"},
        }
        assert n.pending_action_type == "group_invitation"
        assert n.pending_action_id == 11
        assert n.channel == "app"


@pytest.mark.requirement("notifications:R9")
@pytest.mark.asyncio
async def test_notify_for_event_with_empty_recipients_is_noop(db_session: AsyncSession):
    svc = NotificationService(db_session)
    created = await svc.notify_for_event(
        NotificationEvent(
            type="group.invite",
            recipients=[],
            data={"groupId": 1},
        )
    )
    assert created == []
