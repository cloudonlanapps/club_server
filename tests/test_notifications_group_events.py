"""Tests for #47 — group-domain notifications.

Wired events (issue table cross-referenced with the actual code surface):

| Event                    | Recipient        | Category      |
|--------------------------|------------------|---------------|
| group.join_request       | admins           | actionable    |
| group.join_response      | requester        | informational |
| group.member_removed     | removed user     | informational |
| group.archived           | explicit members | informational |
| group.settings_changed   | explicit members | informational |

Events from the issue table that this codebase does **not** support and are
therefore not wired here: group.invite / group.invite_response (no invite
flow), group.member_left (no self-leave endpoint), group.role_changed
(no per-group roles), group.ownership_transfer (no ownership concept).
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import (
    create_admin_user,
    create_member_user,
    create_regular_admin_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


async def _make_manual(client: AsyncClient, admin_token: str, name: str = "M") -> int:
    g = await client.post("/v1/groups", json={"name": name}, headers=auth(admin_token))
    assert g.status_code == 201
    return g.json()["id"]


# ---------------------------------------------------------------------------
# group.join_request — actionable, admins recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R3")
@pytest.mark.requirement("notifications:R31")
@pytest.mark.requirement("notifications:R50")
@pytest.mark.asyncio
async def test_join_request_notifies_admins_and_surfaces_in_pending_actions(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session, "ra")
    member_token = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)

    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert create.status_code == 201
    req_id = create.json()["id"]

    # Both admins receive an actionable notification.
    for admin_name in ("admin", "ra"):
        rows = await _notifications_for(db_session, admin_name, "group.join_request")
        assert len(rows) == 1, (
            f"{admin_name} should receive one join_request notification"
        )
        n = rows[0]
        assert n.pending_action_type == "group_join_request"
        assert n.pending_action_id == req_id
        data: Any = n.payload["data"]
        assert data["groupId"] == gid
        assert data["requesterUsername"] == "amy"

    # The regular admin sees it in pending-actions.
    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(regular_admin_token)
    )
    assert pa.status_code == 200
    assert pa.json()["total"] == 1
    assert pa.json()["items"][0]["pendingActionId"] == req_id


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_join_request_auto_dismisses_when_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)

    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    req_id = create.json()["id"]

    # Approval should auto-dismiss the actionable notification from the feed.
    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{req_id}/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 200

    # Admin's pending-actions is now empty.
    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa.json()["total"] == 0

    # And the actionable notification is also gone from the regular feed (#102).
    feed = await client.get("/v1/notifications", headers=auth(admin_token))
    assert not any(
        item["type"] == "group.join_request" for item in feed.json()["items"]
    )


# ---------------------------------------------------------------------------
# group.join_response — informational, requester recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R51")
@pytest.mark.asyncio
async def test_join_response_approved_notifies_requester(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    amy_token = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(amy_token)
    )
    req_id = create.json()["id"]

    _ = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{req_id}/approve", headers=auth(admin_token)
    )

    rows = await _notifications_for(db_session, "amy", "group.join_response")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["outcome"] == "approved"
    assert data["groupId"] == gid
    assert data["actorUsername"] == "admin"


@pytest.mark.requirement("notifications:R51")
@pytest.mark.asyncio
async def test_join_response_rejected_notifies_requester(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    req_id = create.json()["id"]

    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{req_id}/reject",
        json={"reason": "not now"},
        headers=auth(admin_token),
    )
    assert reject.status_code == 200

    rows = await _notifications_for(db_session, "amy", "group.join_response")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["outcome"] == "rejected"
    assert data["reason"] == "not now"


# ---------------------------------------------------------------------------
# group.member_removed — informational, removed user recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R53")
@pytest.mark.asyncio
async def test_member_removed_notifies_removed_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)

    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )
    assert add.status_code == 201

    remove = await client.delete(
        f"/v1/groups/by_id/{gid}/members/amy", headers=auth(admin_token)
    )
    assert remove.status_code == 200

    rows = await _notifications_for(db_session, "amy", "group.member_removed")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["groupId"] == gid


# ---------------------------------------------------------------------------
# group.archived — informational, members recipients
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R54")
@pytest.mark.asyncio
async def test_archived_notifies_explicit_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    gid = await _make_manual(client, admin_token)

    for u in ("amy", "bob"):
        r = await client.post(
            f"/v1/groups/by_id/{gid}/members/byname/{u}", headers=auth(admin_token)
        )
        assert r.status_code == 201

    archive = await client.delete(f"/v1/groups/by_id/{gid}", headers=auth(admin_token))
    assert archive.status_code == 200

    for u in ("amy", "bob"):
        rows = await _notifications_for(db_session, u, "group.archived")
        assert len(rows) == 1, f"{u} should receive group.archived"
        assert rows[0].payload["data"]["groupId"] == gid

    # The admin (not a member) does not receive it.
    admin_rows = await _notifications_for(db_session, "admin", "group.archived")
    assert admin_rows == []


# ---------------------------------------------------------------------------
# group.settings_changed — informational, members recipients
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R55")
@pytest.mark.asyncio
async def test_settings_changed_notifies_members_with_diff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)
    _ = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    update = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"name": "Renamed", "description": "now with description"},
        headers=auth(admin_token),
    )
    assert update.status_code == 200

    rows = await _notifications_for(db_session, "amy", "group.settings_changed")
    assert len(rows) == 1
    data = rows[0].payload["data"]
    assert data["groupId"] == gid
    assert "name" in data["changes"]
    assert data["changes"]["name"]["to"] == "Renamed"


@pytest.mark.requirement("notifications:R55")
@pytest.mark.asyncio
async def test_settings_changed_skipped_when_no_change(
    client: AsyncClient, db_session: AsyncSession
):
    """Re-PATCHing with the same value is a no-op; no notification fires."""
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    gid = await _make_manual(client, admin_token)
    _ = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    # First PATCH establishes the description.
    _ = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"description": "v1"},
        headers=auth(admin_token),
    )
    # Drain any notifications.
    initial = await _notifications_for(db_session, "amy", "group.settings_changed")
    assert len(initial) == 1

    # Re-PATCH with identical content. ChangeLog should record no diff.
    _ = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={},
        headers=auth(admin_token),
    )

    rows = await _notifications_for(db_session, "amy", "group.settings_changed")
    assert len(rows) == 1, "no additional notification when nothing changed"
