"""The feed, preference and admin-surface rules of notifications_requirements.md (#493).

Covers the rules #401 left [UNTESTED]: soft-deleted recipients, feed order,
the active-account gate, preferences gating nothing, the admin create/delete
surface, unregistered pointer kinds, and the sweep's pointer exemption.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.group import Group
from club_server.db.models.group_join_request import GroupJoinRequest, JoinRequestStatus
from club_server.db.models.notification import Notification
from club_server.db.models.user import User
from club_server.mailer.sender import ConsoleEmailSender
from club_server.services.scheduler import sweep_expired_notifications
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_member_user, create_registered_user
from .redesign_helpers import notifications_for

DAY_MS = 24 * 60 * 60 * 1000


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _row(username: str, created_at: int, **extra: object) -> Notification:
    ntype = str(extra.pop("ntype", "info.note"))
    return Notification(
        username=username,
        type=ntype,
        channel="app",
        payload={"v": 1, "type": ntype, "data": {}},
        is_read=0,
        created_at=created_at,
        **extra,
    )


async def _feed_ids(client: AsyncClient, token: str, path: str = "") -> list[int]:
    response = await client.get(f"/v1/notifications{path}", headers=auth(token))
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


@pytest.mark.requirement("notifications:R6")
@pytest.mark.asyncio
async def test_should_skip_recipient_when_user_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    group = await client.post("/v1/groups", json={"name": "G"}, headers=auth(admin))
    gid = group.json()["id"]
    for name in ("amy", "bob"):
        added = await client.post(
            f"/v1/groups/by_id/{gid}/members/byname/{name}", headers=auth(admin)
        )
        assert added.status_code == 201, added.text
    deleted = await client.delete("/v1/users/by_id/bob", headers=auth(admin))
    assert deleted.status_code == 200, deleted.text

    renamed = await client.patch(
        f"/v1/groups/by_id/{gid}", json={"name": "Renamed"}, headers=auth(admin)
    )

    assert renamed.status_code == 200, renamed.text
    assert (
        len(await notifications_for(db_session, "amy", "group.settings_changed")) == 1
    )
    assert await notifications_for(db_session, "bob", "group.settings_changed") == []


@pytest.mark.requirement("notifications:R11")
@pytest.mark.asyncio
async def test_should_list_feed_newest_first_when_rows_differ_in_age(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    now = now_utc_ms()
    old, new, middle = (
        _row("amy", now - 3000),
        _row("amy", now - 1000),
        _row("amy", now - 2000),
    )
    db_session.add_all([old, new, middle])
    await db_session.flush()
    old_id, new_id, middle_id = old.id, new.id, middle.id
    await db_session.commit()

    ids = await _feed_ids(client, token)

    assert ids == [new_id, middle_id, old_id]


@pytest.mark.requirement("notifications:R11")
@pytest.mark.asyncio
async def test_should_list_pending_actions_newest_first_when_rows_differ_in_age(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    now = now_utc_ms()
    rows = []
    for index, age in enumerate((3000, 1000, 2000)):
        group = Group(name=f"G{index}", kind="manual", created_at=now)
        db_session.add(group)
        await db_session.flush()
        request = GroupJoinRequest(
            group_id=group.id,
            username="amy",
            status=JoinRequestStatus.pending.value,
            requested_at=now,
        )
        db_session.add(request)
        await db_session.flush()
        row = _row(
            "amy",
            now - age,
            ntype="group.join_request",
            pending_action_type="group_join_request",
            pending_action_id=request.id,
        )
        db_session.add(row)
        rows.append(row)
    await db_session.flush()
    row_ids = [r.id for r in rows]
    await db_session.commit()

    ids = await _feed_ids(client, token, "/pending-actions")

    assert ids == [row_ids[1], row_ids[2], row_ids[0]]


@pytest.mark.requirement("auth:R3a")
@pytest.mark.requirement("notifications:R20")
@pytest.mark.asyncio
async def test_should_refuse_feed_actions_when_account_is_not_active(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    row = _row("rosa", now_utc_ms())
    db_session.add(row)
    await db_session.flush()
    await db_session.commit()
    # #516: the unread count, marking read and the preferences moved to the
    # feed's gate (notifications R20a); only the pending actions stay here.
    refused = [
        await client.get("/v1/notifications/pending-actions", headers=auth(token)),
    ]

    for response in refused:
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "ACCOUNT_NOT_ACTIVE"
    feed = await client.get("/v1/notifications", headers=auth(token))
    assert feed.status_code == 200
    assert [item["isRead"] for item in feed.json()["items"]] == [False]


@pytest.mark.requirement("notifications:R24")
@pytest.mark.asyncio
async def test_should_deliver_to_feed_when_every_preference_is_off(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    prefs = await client.patch(
        "/v1/notifications/preferences",
        json={"emailEnabled": False, "pushEnabled": False, "smsEnabled": False},
        headers=auth(token),
    )
    assert prefs.status_code == 200, prefs.text

    role = await client.post(
        "/v1/users/by_id/amy/roles", json={"role": "coach"}, headers=auth(admin)
    )

    assert role.status_code == 200, role.text
    feed = await client.get("/v1/notifications", headers=auth(token))
    assert [item["type"] for item in feed.json()["items"]] == ["user.role_changed"]


@pytest.mark.requirement("notifications:R24")
@pytest.mark.asyncio
async def test_should_email_broadcast_when_recipient_turned_email_off(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    user = (
        await db_session.execute(select(User).where(User.username == "amy"))
    ).scalar_one()
    user.email = "amy@example.com"
    await db_session.commit()
    prefs = await client.patch(
        "/v1/notifications/preferences",
        json={"emailEnabled": False},
        headers=auth(token),
    )
    assert prefs.json()["emailEnabled"] is False
    ConsoleEmailSender.clear()

    sent = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "users", "usernames": ["amy"]},
            "payload": {"v": 1, "type": "broadcast.message", "data": {}},
            "email": True,
            "emailSubject": "Ice time",
            "emailBody": "Rink closed tonight.",
        },
        headers=auth(admin),
    )

    assert sent.status_code == 201, sent.text
    assert [m.to for m in ConsoleEmailSender.outbox] == ["amy@example.com"]
    ConsoleEmailSender.clear()


@pytest.mark.requirement("notifications:R26")
@pytest.mark.asyncio
async def test_should_refuse_create_when_caller_is_not_admin(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    await db_session.commit()

    response = await client.post(
        "/v1/notifications",
        json={
            "username": "bob",
            "type": "info.note",
            "channel": "app",
            "payload": {"v": 1, "type": "info.note", "data": {}},
        },
        headers=auth(token),
    )

    assert response.status_code == 403, response.text
    assert await notifications_for(db_session, "bob", "info.note") == []


@pytest.mark.requirement("notifications:R28")
@pytest.mark.asyncio
async def test_should_remove_from_feed_when_admin_deletes_notification(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    keep, drop = _row("amy", now_utc_ms()), _row("amy", now_utc_ms())
    db_session.add_all([keep, drop])
    await db_session.flush()
    keep_id, drop_id = keep.id, drop.id
    await db_session.commit()

    response = await client.delete(
        f"/v1/notifications/by_id/{drop_id}", headers=auth(admin)
    )

    assert response.status_code == 204, response.text
    assert await _feed_ids(client, token) == [keep_id]


@pytest.mark.requirement("notifications:R29")
@pytest.mark.asyncio
async def test_should_audit_when_admin_creates_and_deletes_notification(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    created = await client.post(
        "/v1/notifications",
        json={
            "username": "amy",
            "type": "info.note",
            "channel": "app",
            "payload": {"v": 1, "type": "info.note", "data": {}},
        },
        headers=auth(admin),
    )
    assert created.status_code == 201, created.text
    nid = created.json()["id"]

    deleted = await client.delete(f"/v1/notifications/by_id/{nid}", headers=auth(admin))

    assert deleted.status_code == 204, deleted.text
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.resource_type == "notification")
            )
        )
        .scalars()
        .all()
    )
    by_action = {row.action: row for row in rows}
    assert set(by_action) == {"create_notification", "delete_notification"}
    assert all(row.actor_username == "admin" for row in rows)
    assert all(row.resource_id == str(nid) for row in rows)
    assert by_action["create_notification"].target_username == "amy"


@pytest.mark.requirement("notifications:R33")
@pytest.mark.asyncio
async def test_should_leave_out_of_pending_actions_when_pointer_kind_is_unregistered(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    token = await create_member_user(db_session, "amy")
    created = await client.post(
        "/v1/notifications",
        json={
            "username": "amy",
            "type": "group.invite",
            "channel": "app",
            "payload": {"v": 1, "type": "group.invite", "data": {}},
            "pendingActionType": "group_invitation",
            "pendingActionId": 1,
        },
        headers=auth(admin),
    )
    assert created.status_code == 201, created.text

    pending = await _feed_ids(client, token, "/pending-actions")

    assert pending == []
    assert await _feed_ids(client, token) == [created.json()["id"]]


@pytest.mark.requirement("notifications:R46")
@pytest.mark.asyncio
async def test_should_keep_actionable_row_when_sweep_runs_after_its_action_resolved(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    now = now_utc_ms()
    group = Group(name="G", kind="manual", created_at=now)
    db_session.add(group)
    await db_session.flush()
    request = GroupJoinRequest(
        group_id=group.id,
        username="admin",
        status=JoinRequestStatus.approved.value,
        requested_at=now,
    )
    db_session.add(request)
    await db_session.flush()
    stale = _row(
        "admin",
        now - 200 * DAY_MS,
        ntype="group.join_request",
        pending_action_type="group_join_request",
        pending_action_id=request.id,
    )
    db_session.add(stale)
    await db_session.flush()

    deleted = await sweep_expired_notifications(db_session, now)

    assert deleted == 0
    remaining = (await db_session.execute(select(Notification))).scalars().all()
    assert [n.id for n in remaining] == [stale.id]
