"""Which notifications reach a blocked user (#512).

notifications R7b: a blocked user receives only the notices about their
account (the allow-list); club activity — group, event, enrollment
notices and broadcasts — is suppressed for them, while an active user in
the same audience still receives it.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .enrollment_rejoin_helpers import remove
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    assign,
    auth,
    create_programme,
    create_venue,
    notifications_for,
)

PAYLOAD = {"v": 1, "type": "broadcast.message", "data": {"text": "Hello"}}


async def _block(client: AsyncClient, admin: str, username: str) -> None:
    response = await client.post(
        f"/v1/users/by_id/{username}/block", headers=auth(admin)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"


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


async def _event_with(client: AsyncClient, admin: str, *members: str) -> int:
    venue = await create_venue(client, admin)
    event_id = (await create_programme(client, admin, venue))["id"]
    assigned = await assign(client, admin, event_id, *members)
    assert assigned.status_code == 204, assigned.text
    return event_id


async def _admin_amy_and_bob(client: AsyncClient, db_session: AsyncSession) -> str:
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    await db_session.commit()
    return admin


# ---------------------------------------------------------------------------
# Delivered: notices about the account itself
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_deliver_role_change_when_user_is_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    await _block(client, admin, "amy")

    response = await client.post(
        "/v1/users/by_id/amy/roles", json={"role": "coach"}, headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    assert len(await notifications_for(db_session, "amy", "user.blocked")) == 1
    assert len(await notifications_for(db_session, "amy", "user.role_changed")) == 1


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_deliver_password_reset_notice_when_user_is_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    await _block(client, admin, "amy")

    response = await client.post("/v1/admin/reset-password/amy", headers=auth(admin))

    assert response.status_code == 200, response.text
    rows = await notifications_for(
        db_session, "amy", "account.password_changed_by_admin"
    )
    assert len(rows) == 1


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_deliver_profile_change_when_user_is_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    await _block(client, admin, "amy")

    response = await client.patch(
        "/v1/users/by_id/amy", json={"firstName": "Amelia"}, headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    rows = await notifications_for(db_session, "amy", "profile.changed_by_admin")
    assert len(rows) == 1


@pytest.mark.requirement("notifications:R27a")
@pytest.mark.asyncio
async def test_should_create_admin_notification_when_blocked_user_gets_account_type(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    await _block(client, admin, "amy")

    response = await client.post(
        "/v1/notifications",
        json={
            "username": "amy",
            "type": "user.role_changed",
            "channel": "app",
            "payload": {"v": 1, "type": "user.role_changed", "data": {}},
        },
        headers=auth(admin),
    )

    assert response.status_code == 201, response.text
    assert len(await notifications_for(db_session, "amy", "user.role_changed")) == 1


# ---------------------------------------------------------------------------
# Suppressed: club activity
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_skip_blocked_user_when_group_notice_names_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    gid = await _group_with(client, admin, "amy", "bob")
    await _block(client, admin, "amy")

    for name in ("amy", "bob"):
        removed = await client.delete(
            f"/v1/groups/by_id/{gid}/members/{name}", headers=auth(admin)
        )
        assert removed.status_code == 200, removed.text

    assert await notifications_for(db_session, "amy", "group.member_removed") == []
    assert len(await notifications_for(db_session, "bob", "group.member_removed")) == 1


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_skip_blocked_member_when_group_notice_goes_to_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    gid = await _group_with(client, admin, "amy", "bob")
    await _block(client, admin, "amy")

    archived = await client.delete(f"/v1/groups/by_id/{gid}", headers=auth(admin))

    assert archived.status_code == 200, archived.text
    assert await notifications_for(db_session, "amy", "group.archived") == []
    assert len(await notifications_for(db_session, "bob", "group.archived")) == 1


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_skip_blocked_member_when_event_notice_goes_to_enrolled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    event_id = await _event_with(client, admin, "amy", "bob")
    await _block(client, admin, "amy")

    deleted = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(admin))

    assert deleted.status_code == 200, deleted.text
    assert await notifications_for(db_session, "amy", "event.deleted") == []
    assert len(await notifications_for(db_session, "bob", "event.deleted")) == 1


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_skip_blocked_member_when_enrollment_is_removed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    event_id = await _event_with(client, admin, "amy", "bob")
    await _block(client, admin, "amy")

    for name in ("amy", "bob"):
        removed = await remove(client, admin, event_id, name, reason="Roster")
        assert removed.status_code in (200, 204), removed.text

    assert (
        await notifications_for(db_session, "amy", "enrollment.cancelled_admin") == []
    )
    bob_rows = await notifications_for(db_session, "bob", "enrollment.cancelled_admin")
    assert len(bob_rows) == 1


@pytest.mark.requirement("notifications:R7b")
@pytest.mark.asyncio
async def test_should_skip_blocked_member_when_broadcast_targets_a_group(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    gid = await _group_with(client, admin, "amy", "bob")
    await _block(client, admin, "amy")

    response = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "group", "groupId": gid},
            "payload": PAYLOAD,
        },
        headers=auth(admin),
    )

    assert response.status_code == 201, response.text
    assert response.json()["recipientCount"] == 1
    assert await notifications_for(db_session, "amy", "broadcast.message") == []
    assert len(await notifications_for(db_session, "bob", "broadcast.message")) == 1


@pytest.mark.requirement("notifications:R27a")
@pytest.mark.asyncio
async def test_should_refuse_admin_notification_when_blocked_user_gets_club_type(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _admin_amy_and_bob(client, db_session)
    await _block(client, admin, "amy")

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
        select(Notification).where(
            Notification.username == "amy", Notification.type == "custom.notice"
        )
    )
    assert rows.scalars().all() == []
