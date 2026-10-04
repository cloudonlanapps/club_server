"""Tests for GET /v1/notifications/pending-actions and auto-dismiss.

The endpoint surfaces actionable notifications whose linked pending-action
row is still unresolved. Notifications without a registered
``pending_action_type``, or whose linked row has reached a terminal state,
must not appear here.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.group import Group
from club_server.db.models.group_join_request import GroupJoinRequest, JoinRequestStatus
from club_server.db.models.notification import Notification
from club_server.utils import now_utc_ms

from .helpers import create_admin_user
from .test_notifications import create_user_and_get_token, make_payload


async def _create_group(db_session: AsyncSession, name: str = "U-12") -> Group:
    group = Group(name=name, kind="manual", created_at=now_utc_ms())
    db_session.add(group)
    await db_session.commit()
    await db_session.refresh(group)
    return group


async def _create_join_request(
    db_session: AsyncSession,
    group_id: int,
    username: str,
    status: JoinRequestStatus = JoinRequestStatus.pending,
) -> GroupJoinRequest:
    req = GroupJoinRequest(
        group_id=group_id,
        username=username,
        status=status.value,
        requested_at=now_utc_ms(),
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)
    return req


async def _create_actionable_notification(
    db_session: AsyncSession,
    username: str,
    pending_action_type: str,
    pending_action_id: int,
    event_type: str = "group.join_request",
) -> Notification:
    notification = Notification(
        username=username,
        type=event_type,
        channel="app",
        payload=make_payload(event_type, groupId=pending_action_id),
        pending_action_type=pending_action_type,
        pending_action_id=pending_action_id,
        created_at=now_utc_ms(),
    )
    db_session.add(notification)
    await db_session.commit()
    await db_session.refresh(notification)
    return notification


@pytest.mark.requirement("notifications:R41")
@pytest.mark.asyncio
async def test_pending_actions_unauthenticated_rejected(client: AsyncClient):
    response = await client.get("/v1/notifications/pending-actions")
    assert response.status_code == 401


@pytest.mark.requirement("notifications:R30")
@pytest.mark.asyncio
async def test_pending_actions_empty(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )

    response = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.requirement("notifications:R30")
@pytest.mark.asyncio
async def test_pending_action_visible_while_unresolved(
    client: AsyncClient, db_session: AsyncSession
):
    """A notification linked to a pending join request appears in the feed."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )

    group = await _create_group(db_session)
    req = await _create_join_request(db_session, group.id, "alice")
    req_id = req.id
    n = await _create_actionable_notification(
        db_session, "alice", "group_join_request", req_id
    )
    n_id = n.id

    response = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == n_id
    assert item["pendingActionType"] == "group_join_request"
    assert item["pendingActionId"] == req_id


@pytest.mark.requirement("notifications:R34")
@pytest.mark.requirement("notifications:R35")
@pytest.mark.asyncio
async def test_pending_action_dismissed_when_source_resolved(
    client: AsyncClient, db_session: AsyncSession
):
    """Once the linked join request transitions to a terminal state, the
    actionable notification disappears from this endpoint — but remains in
    the regular notifications feed as historical record."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )

    group = await _create_group(db_session)
    req = await _create_join_request(db_session, group.id, "alice")
    req_id = req.id
    n = await _create_actionable_notification(
        db_session, "alice", "group_join_request", req_id
    )
    n_id = n.id

    # Sanity check — visible before resolution.
    pre = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert pre.json()["total"] == 1

    # Resolve the source pending action.
    req.status = JoinRequestStatus.approved.value
    req.decided_at = now_utc_ms()
    await db_session.commit()

    post = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert post.status_code == 200
    assert post.json()["total"] == 0
    assert post.json()["items"] == []

    # The notification is still in the regular feed as historical record.
    feed = await client.get(
        "/v1/notifications",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    feed_ids = [item["id"] for item in feed.json()["items"]]
    assert n_id in feed_ids


@pytest.mark.requirement("notifications:R40")
@pytest.mark.asyncio
async def test_pending_actions_isolated_per_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    alice_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )
    _ = await create_user_and_get_token(client, admin_token, "bob", db_session)

    group = await _create_group(db_session)
    req_a = await _create_join_request(db_session, group.id, "alice")
    req_b = await _create_join_request(db_session, group.id, "bob")
    _ = await _create_actionable_notification(
        db_session, "alice", "group_join_request", req_a.id
    )
    _ = await _create_actionable_notification(
        db_session, "bob", "group_join_request", req_b.id
    )

    response = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["username"] == "alice"


@pytest.mark.requirement("notifications:R32")
@pytest.mark.asyncio
async def test_pending_actions_excludes_unregistered_types_and_plain_notifications(
    client: AsyncClient, db_session: AsyncSession
):
    """Notifications without a pending_action_type, or whose type is not
    yet registered, must not surface in the actionable feed."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )

    # Plain informational notification (no pending action).
    n_plain = Notification(
        username="alice",
        type="group.archived",
        channel="app",
        payload=make_payload("group.archived", groupId=1),
        created_at=now_utc_ms(),
    )
    # Actionable but pointing to a type not yet wired into the registry.
    n_unregistered = Notification(
        username="alice",
        type="enrollment.opened",
        channel="app",
        payload=make_payload("enrollment.opened", eventId=7),
        pending_action_type="enrollment_opportunity",
        pending_action_id=7,
        created_at=now_utc_ms(),
    )
    db_session.add_all([n_plain, n_unregistered])
    await db_session.commit()

    response = await client.get(
        "/v1/notifications/pending-actions",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.requirement("notifications:R30")
@pytest.mark.asyncio
async def test_pending_actions_pagination(
    client: AsyncClient, db_session: AsyncSession
):
    """Three distinct groups, one pending request + actionable notification
    each — pagination walks the result set."""
    admin_token = await create_admin_user(db_session)
    user_token = await create_user_and_get_token(
        client, admin_token, "alice", db_session
    )

    for i in range(3):
        g = await _create_group(db_session, f"G{i}")
        req = await _create_join_request(db_session, g.id, "alice")
        _ = await _create_actionable_notification(
            db_session, "alice", "group_join_request", req.id
        )

    response = await client.get(
        "/v1/notifications/pending-actions?offset=0&limit=2",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    response2 = await client.get(
        "/v1/notifications/pending-actions?offset=2&limit=2",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response2.status_code == 200
    assert response2.json()["total"] == 3
    assert len(response2.json()["items"]) == 1
