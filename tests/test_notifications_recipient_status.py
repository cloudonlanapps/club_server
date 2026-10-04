"""Which account statuses a notification reaches (#511, #512).

notifications R7a: a user who has left receives no notification of any
kind — a named-user notice, a group or event notice, a broadcast. An active
user in the same audience still receives theirs.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    assign,
    auth,
    create_programme,
    create_venue,
    notifications_for,
)

PAYLOAD = {"v": 1, "type": "broadcast.message", "data": {"text": "Hello"}}


async def _group_with(client: AsyncClient, admin: str, *members: str) -> int:
    created = await client.post("/v1/groups", json={"name": "G"}, headers=auth(admin))
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    for name in members:
        added = await client.post(
            f"/v1/groups/by_id/{gid}/members/byname/{name}", headers=auth(admin)
        )
        assert added.status_code == 201, added.text
    return gid


async def _mark_left(client: AsyncClient, admin: str, username: str) -> None:
    response = await client.post(
        f"/v1/users/by_id/{username}/mark-left", headers=auth(admin)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "left"


async def _event_with(client: AsyncClient, admin: str, *members: str) -> int:
    venue = await create_venue(client, admin)
    event_id = (await create_programme(client, admin, venue))["id"]
    assigned = await assign(client, admin, event_id, *members)
    assert assigned.status_code == 204, assigned.text
    return event_id


async def _broadcast(client: AsyncClient, admin: str, selector: dict) -> int:
    response = await client.post(
        "/v1/broadcasts",
        json={"audienceSelector": selector, "payload": PAYLOAD},
        headers=auth(admin),
    )
    assert response.status_code == 201, response.text
    return response.json()["recipientCount"]


@pytest.mark.requirement("notifications:R7a")
@pytest.mark.asyncio
async def test_should_skip_left_user_when_notice_names_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    gid = await _group_with(client, admin, "amy", "bob")
    await _mark_left(client, admin, "amy")

    for name in ("amy", "bob"):
        removed = await client.delete(
            f"/v1/groups/by_id/{gid}/members/{name}", headers=auth(admin)
        )
        assert removed.status_code == 200, removed.text

    assert await notifications_for(db_session, "amy", "group.member_removed") == []
    assert len(await notifications_for(db_session, "bob", "group.member_removed")) == 1


@pytest.mark.requirement("notifications:R7a")
@pytest.mark.asyncio
async def test_should_skip_left_member_when_group_notice_goes_to_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    gid = await _group_with(client, admin, "amy", "bob")
    await _mark_left(client, admin, "amy")

    archived = await client.delete(f"/v1/groups/by_id/{gid}", headers=auth(admin))

    assert archived.status_code == 200, archived.text
    assert await notifications_for(db_session, "amy", "group.archived") == []
    assert len(await notifications_for(db_session, "bob", "group.archived")) == 1


@pytest.mark.requirement("notifications:R7a")
@pytest.mark.asyncio
async def test_should_skip_left_member_when_event_notice_goes_to_enrolled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    event_id = await _event_with(client, admin, "amy", "bob")
    await _mark_left(client, admin, "amy")

    deleted = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(admin))

    assert deleted.status_code == 200, deleted.text
    assert await notifications_for(db_session, "amy", "event.deleted") == []
    assert len(await notifications_for(db_session, "bob", "event.deleted")) == 1


@pytest.mark.requirement("notifications:R7a")
@pytest.mark.asyncio
async def test_should_skip_left_member_when_broadcast_targets_a_group(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    gid = await _group_with(client, admin, "amy", "bob")
    await _mark_left(client, admin, "amy")

    count = await _broadcast(client, admin, {"kind": "group", "groupId": gid})

    assert count == 1
    assert await notifications_for(db_session, "amy", "broadcast.message") == []
    assert len(await notifications_for(db_session, "bob", "broadcast.message")) == 1


@pytest.mark.requirement("notifications:R7a")
@pytest.mark.asyncio
async def test_should_skip_left_member_when_broadcast_targets_event_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    event_id = await _event_with(client, admin, "amy", "bob")
    await _mark_left(client, admin, "amy")

    count = await _broadcast(
        client, admin, {"kind": "event_members", "eventId": event_id}
    )

    assert count == 1
    assert await notifications_for(db_session, "amy", "broadcast.message") == []
    assert len(await notifications_for(db_session, "bob", "broadcast.message")) == 1


@pytest.mark.requirement("notifications:R6")
@pytest.mark.asyncio
async def test_should_skip_soft_deleted_member_when_broadcast_targets_a_group(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    gid = await _group_with(client, admin, "amy", "bob")
    deleted = await client.delete("/v1/users/by_id/amy", headers=auth(admin))
    assert deleted.status_code == 200, deleted.text

    count = await _broadcast(client, admin, {"kind": "group", "groupId": gid})

    assert count == 1
    assert await notifications_for(db_session, "amy", "broadcast.message") == []
    assert len(await notifications_for(db_session, "bob", "broadcast.message")) == 1


@pytest.mark.requirement("notifications:R27a")
@pytest.mark.asyncio
async def test_should_refuse_admin_notification_when_recipient_has_left(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()
    await _mark_left(client, admin, "amy")

    response = await client.post(
        "/v1/notifications",
        json={
            "username": "amy",
            "type": "custom.notice",
            "channel": "app",
            "payload": {"v": 1, "type": "custom.notice", "data": {}},
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "RECIPIENT_NOT_DELIVERABLE"
    db_session.expire_all()
    rows = await db_session.execute(
        select(Notification).where(Notification.username == "amy")
    )
    assert rows.scalars().all() == []
