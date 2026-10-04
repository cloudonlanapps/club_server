"""Tests for #51 — broadcasts feature.

Broadcasts are admin-authored fan-out messages. One ``Broadcast`` row
records the audience selector + payload; one ``Notification`` row per
resolved recipient delivers the message. Recipients see broadcasts in
their normal `/v1/notifications` feed. Read-state aggregation is
derived by counting linked notifications.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.broadcast import Broadcast, BroadcastStatus
from club_server.db.models.notification import Notification

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_time_ms(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


PAYLOAD = {"v": 1, "type": "broadcast.message", "data": {"text": "Hello"}}


async def _register_member(
    client: AsyncClient, admin_token: str, username: str, db_session: AsyncSession
) -> str:
    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "pw123",
            "firstName": "T",
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


async def _make_group(client: AsyncClient, admin_token: str, name: str = "G") -> int:
    g = await client.post("/v1/groups", json={"name": name}, headers=auth(admin_token))
    return g.json()["id"]


async def _add_group_member(
    client: AsyncClient, admin_token: str, gid: int, username: str
) -> None:
    r = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/{username}",
        headers=auth(admin_token),
    )
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# auth + scope
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R125")
@pytest.mark.asyncio
async def test_create_broadcast_requires_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await _register_member(client, admin_token, "alice", db_session)

    response = await client.post(
        "/v1/broadcasts",
        json={"audienceSelector": {"kind": "all_users"}, "payload": PAYLOAD},
        headers=auth(member_token),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# audience selectors
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R113")
@pytest.mark.requirement("notifications:R119")
@pytest.mark.asyncio
async def test_audience_all_users(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)

    response = await client.post(
        "/v1/broadcasts",
        json={"audienceSelector": {"kind": "all_users"}, "payload": PAYLOAD},
        headers=auth(admin_token),
    )
    assert response.status_code == 201
    body = response.json()
    # admin + alice + bob = 3 active users
    assert body["recipientCount"] == 3

    # alice and bob both see the broadcast in their feed.
    alice_login = await client.post(
        "/v1/auth/login", json={"username": "alice", "password": "pw123"}
    )
    alice_token = alice_login.json()["accessToken"]
    feed = await client.get("/v1/notifications", headers=auth(alice_token))
    assert any(item["type"] == "broadcast.message" for item in feed.json()["items"])


@pytest.mark.requirement("notifications:R113")
@pytest.mark.asyncio
async def test_audience_role_admin(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")
    _ = await create_coach_user(db_session, "ca")
    _ = await create_member_user(db_session, "m1")

    response = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "role", "role": "admin"},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201
    # admin (super) + ra → 2; coach and member excluded
    assert response.json()["recipientCount"] == 2


@pytest.mark.requirement("notifications:R113")
@pytest.mark.asyncio
async def test_audience_role_coach(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ca1")
    _ = await create_coach_user(db_session, "ca2")
    _ = await create_member_user(db_session, "m1")

    response = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "role", "role": "coach"},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["recipientCount"] == 2


@pytest.mark.requirement("notifications:R114")
@pytest.mark.asyncio
async def test_audience_users_list(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)
    _ = await _register_member(client, admin_token, "carol", db_session)

    response = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {
                "kind": "users",
                "usernames": ["alice", "carol", "ghost"],
            },
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201
    # ghost dropped silently; alice + carol remain.
    assert response.json()["recipientCount"] == 2


@pytest.mark.requirement("notifications:R113")
@pytest.mark.asyncio
async def test_audience_group_members(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)
    _ = await _register_member(client, admin_token, "carol", db_session)
    gid = await _make_group(client, admin_token)
    await _add_group_member(client, admin_token, gid, "alice")
    await _add_group_member(client, admin_token, gid, "bob")

    response = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "group", "groupId": gid},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["recipientCount"] == 2  # alice + bob; carol not a member


@pytest.mark.requirement("notifications:R113")
@pytest.mark.asyncio
async def test_audience_event_members(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)

    venue = await client.post(
        "/v1/venues", json={"name": "V"}, headers=auth(admin_token)
    )
    event = await client.post(
        "/v1/events",
        json={
            "title": "E",
            "type": "programme",
            "venueId": venue.json()["id"],
            "startTimeUtc": future_time_ms(24),
            "endTimeUtc": future_time_ms(25),
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    response = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "event_members", "eventId": event_id},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["recipientCount"] == 1


@pytest.mark.requirement("notifications:R117")
@pytest.mark.asyncio
async def test_invalid_audience_selector_missing_kind(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/broadcasts",
        json={"audienceSelector": {}, "payload": PAYLOAD},
        headers=auth(admin_token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_AUDIENCE_SELECTOR"


@pytest.mark.requirement("notifications:R117")
@pytest.mark.asyncio
async def test_invalid_audience_selector_bogus_kind(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/broadcasts",
        json={"audienceSelector": {"kind": "everyone"}, "payload": PAYLOAD},
        headers=auth(admin_token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_AUDIENCE_SELECTOR"


# ---------------------------------------------------------------------------
# fan-out + read tracking
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R112")
@pytest.mark.asyncio
async def test_fan_out_creates_one_notification_per_recipient(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)

    create = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "users", "usernames": ["alice", "bob"]},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    broadcast_id = create.json()["id"]

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(Notification).where(Notification.broadcast_id == broadcast_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    usernames = {n.username for n in rows}
    assert usernames == {"alice", "bob"}
    for n in rows:
        assert n.payload == PAYLOAD
        assert n.type == "broadcast.message"
        assert n.broadcast_id == broadcast_id


@pytest.mark.requirement("notifications:R4")
@pytest.mark.requirement("notifications:R121")
@pytest.mark.asyncio
async def test_read_tracking_aggregates(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    alice_token = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)

    create = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "users", "usernames": ["alice", "bob"]},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    broadcast_id = create.json()["id"]

    # Alice marks her broadcast notification read via the regular feed.
    feed = await client.get("/v1/notifications", headers=auth(alice_token))
    her_notif_id = next(
        i["id"] for i in feed.json()["items"] if i["type"] == "broadcast.message"
    )
    _ = await client.post(
        f"/v1/notifications/by_id/{her_notif_id}/read", headers=auth(alice_token)
    )

    detail = await client.get(
        f"/v1/broadcasts/by_id/{broadcast_id}", headers=auth(admin_token)
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["readCount"] == 1
    assert body["unreadCount"] == 1
    assert body["recipientCount"] == 2


@pytest.mark.requirement("notifications:R122")
@pytest.mark.asyncio
async def test_recipients_endpoint_filters_by_status(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    alice_token = await _register_member(client, admin_token, "alice", db_session)
    _ = await _register_member(client, admin_token, "bob", db_session)

    create = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "users", "usernames": ["alice", "bob"]},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    broadcast_id = create.json()["id"]

    feed = await client.get("/v1/notifications", headers=auth(alice_token))
    her_notif_id = next(
        i["id"] for i in feed.json()["items"] if i["type"] == "broadcast.message"
    )
    _ = await client.post(
        f"/v1/notifications/by_id/{her_notif_id}/read", headers=auth(alice_token)
    )

    read = await client.get(
        f"/v1/broadcasts/by_id/{broadcast_id}/recipients?status=read",
        headers=auth(admin_token),
    )
    assert read.status_code == 200
    assert read.json()["total"] == 1
    assert read.json()["items"][0]["username"] == "alice"
    assert read.json()["items"][0]["isRead"] is True

    unread = await client.get(
        f"/v1/broadcasts/by_id/{broadcast_id}/recipients?status=unread",
        headers=auth(admin_token),
    )
    assert unread.json()["total"] == 1
    assert unread.json()["items"][0]["username"] == "bob"


# ---------------------------------------------------------------------------
# list / revoke
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R120")
@pytest.mark.asyncio
async def test_list_broadcasts_newest_first(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)

    body = {
        "audienceSelector": {"kind": "users", "usernames": ["alice"]},
        "payload": PAYLOAD,
    }
    first = await client.post("/v1/broadcasts", json=body, headers=auth(admin_token))
    second = await client.post("/v1/broadcasts", json=body, headers=auth(admin_token))

    listing = await client.get("/v1/broadcasts", headers=auth(admin_token))
    assert listing.status_code == 200
    ids = [item["id"] for item in listing.json()["items"]]
    # Newest first.
    assert ids == [second.json()["id"], first.json()["id"]]


@pytest.mark.requirement("notifications:R123")
@pytest.mark.asyncio
async def test_revoke_deletes_fan_out_keeps_broadcast(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)

    create = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "users", "usernames": ["alice"]},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    broadcast_id = create.json()["id"]

    revoke = await client.delete(
        f"/v1/broadcasts/by_id/{broadcast_id}", headers=auth(admin_token)
    )
    assert revoke.status_code == 200

    # The broadcast row remains for audit but its status is 'revoked'.
    db_session.expire_all()
    broadcast = (
        await db_session.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
    ).scalar_one_or_none()
    assert broadcast is not None
    assert broadcast.status == BroadcastStatus.revoked.value

    # Fan-out rows are gone.
    fan_out = (
        (
            await db_session.execute(
                select(Notification).where(Notification.broadcast_id == broadcast_id)
            )
        )
        .scalars()
        .all()
    )
    assert fan_out == []


@pytest.mark.requirement("notifications:R124")
@pytest.mark.asyncio
async def test_audit_log_on_create_and_revoke(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _register_member(client, admin_token, "alice", db_session)

    create = await client.post(
        "/v1/broadcasts",
        json={
            "audienceSelector": {"kind": "users", "usernames": ["alice"]},
            "payload": PAYLOAD,
        },
        headers=auth(admin_token),
    )
    broadcast_id = create.json()["id"]

    _ = await client.delete(
        f"/v1/broadcasts/by_id/{broadcast_id}", headers=auth(admin_token)
    )

    db_session.expire_all()
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource_type == "broadcast",
                    AuditLog.resource_id == str(broadcast_id),
                )
            )
        )
        .scalars()
        .all()
    )
    actions = {a.action for a in audits}
    assert {"create_broadcast", "revoke_broadcast"} == actions


@pytest.mark.requirement("notifications:R127")
@pytest.mark.asyncio
async def test_revoke_unknown_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    response = await client.delete(
        "/v1/broadcasts/by_id/9999", headers=auth(admin_token)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "BROADCAST_NOT_FOUND"
