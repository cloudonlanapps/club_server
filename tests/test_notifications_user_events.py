"""Tests for #68 — user-domain notifications.

| Event                       | Recipient        | Category      |
|-----------------------------|------------------|---------------|
| user.registration_pending   | admins           | actionable    |
| account.registration_approved               | affected user    | informational |
| user.blocked                | affected user    | informational |
| user.unblocked              | affected user    | informational |
| user.role_changed           | affected user    | informational |
| user.deleted                | admins           | informational |
| user.restored               | affected user    | informational |
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import (
    attach_identity_document,
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


async def _register_pending(
    client: AsyncClient,
    username: str = "newbie",
    db_session: AsyncSession | None = None,
) -> None:
    """Register a new user and submit them for admin review.

    Under #120 the ``user_approval`` notification is deferred until the
    user signals completion via ``POST /v1/users/me/submit-for-review``,
    so this helper performs both steps to land the user at
    ``status=pending`` with the admin notification enqueued.
    """
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "pw123",
            "firstName": "New",
            "lastName": "Bie",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 201, response.text

    login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "pw123"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["accessToken"]

    assert db_session is not None, "_register_pending requires db_session"
    await attach_identity_document(db_session, username)
    submit = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert submit.status_code == 200, submit.text


# ---------------------------------------------------------------------------
# user.registration_pending — actionable, admins recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R97")
@pytest.mark.asyncio
async def test_registration_notifies_all_active_admins(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")

    await _register_pending(client, "newbie", db_session)

    for admin_name in ("admin", "ra"):
        rows = await _notifications_for(
            db_session, admin_name, "user.registration_pending"
        )
        assert len(rows) == 1, (
            f"{admin_name} should receive one registration_pending notification"
        )
        n = rows[0]
        assert n.pending_action_type == "user_approval"
        assert n.pending_action_key == "newbie"
        data: Any = n.payload["data"]
        assert data["username"] == "newbie"
        assert data["firstName"] == "New"


@pytest.mark.requirement("notifications:R31")
@pytest.mark.asyncio
async def test_registration_pending_surfaces_in_pending_actions(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session, "ra")

    await _register_pending(client, "newbie", db_session)

    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(regular_admin_token)
    )
    assert pa.status_code == 200
    body = pa.json()
    assert body["total"] == 1
    assert body["items"][0]["pendingActionKey"] == "newbie"


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_registration_pending_auto_dismisses_when_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session, "ra")

    await _register_pending(client, "newbie", db_session)

    # Before approval: pending action is present.
    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(regular_admin_token)
    )
    assert pa.json()["total"] == 1

    approve = await client.post(
        "/v1/users/by_id/newbie/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 200

    # After approval: pending action is auto-dismissed for both admins.
    for token in (admin_token, regular_admin_token):
        pa = await client.get("/v1/notifications/pending-actions", headers=auth(token))
        assert pa.status_code == 200
        assert pa.json()["total"] == 0


@pytest.mark.requirement("notifications:R34")
@pytest.mark.asyncio
async def test_registration_pending_auto_dismisses_when_user_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)

    await _register_pending(client, "newbie", db_session)

    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa.json()["total"] == 1

    deleted = await client.delete("/v1/users/by_id/newbie", headers=auth(admin_token))
    assert deleted.status_code == 200

    pa = await client.get(
        "/v1/notifications/pending-actions", headers=auth(admin_token)
    )
    assert pa.json()["total"] == 0


# ---------------------------------------------------------------------------
# account.registration_approved — informational, user recipient
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R98")
@pytest.mark.asyncio
async def test_approve_notifies_user(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    await _register_pending(client, "newbie", db_session)

    approve = await client.post(
        "/v1/users/by_id/newbie/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 200

    rows = await _notifications_for(
        db_session, "newbie", "account.registration_approved"
    )
    assert len(rows) == 1
    assert rows[0].pending_action_type is None
    assert rows[0].payload["data"]["username"] == "newbie"


# ---------------------------------------------------------------------------
# user.blocked / user.unblocked
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R7")
@pytest.mark.requirement("notifications:R99")
@pytest.mark.asyncio
async def test_block_notifies_user(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    response = await client.post("/v1/users/by_id/amy/block", headers=auth(admin_token))
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "user.blocked")
    assert len(rows) == 1
    assert rows[0].payload["data"]["username"] == "amy"


@pytest.mark.requirement("notifications:R99")
@pytest.mark.asyncio
async def test_unblock_notifies_user(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    _ = await client.post("/v1/users/by_id/amy/block", headers=auth(admin_token))
    response = await client.post(
        "/v1/users/by_id/amy/unblock", headers=auth(admin_token)
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "user.unblocked")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# user.role_changed
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R100")
@pytest.mark.asyncio
async def test_assign_role_notifies_user_with_diff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    response = await client.post(
        "/v1/users/by_id/amy/roles",
        json={"role": "coach"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "user.role_changed")
    assert len(rows) == 1
    data: Any = rows[0].payload["data"]
    assert data["added"] == ["coach"]
    assert data["removed"] == []
    assert data["newRoles"] == ["coach"]
    assert data["oldRoles"] == []
    assert data["actorUsername"] == "admin"


@pytest.mark.requirement("notifications:R100")
@pytest.mark.asyncio
async def test_remove_role_notifies_user_with_diff(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    _ = await client.post(
        "/v1/users/by_id/amy/roles",
        json={"role": "coach"},
        headers=auth(admin_token),
    )
    response = await client.delete(
        "/v1/users/by_id/amy/roles/coach", headers=auth(admin_token)
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "user.role_changed")
    assert len(rows) == 2
    rows.sort(key=lambda n: n.created_at)
    latest_data: Any = rows[-1].payload["data"]
    assert latest_data["removed"] == ["coach"]
    assert latest_data["newRoles"] == []
    assert latest_data["actorUsername"] == "admin"


@pytest.mark.requirement("notifications:R101")
@pytest.mark.asyncio
async def test_transfer_superadmin_notifies_both_parties(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")

    response = await client.post(
        "/v1/users/by_id/ra/transfer-superadmin", headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text

    outgoing = await _notifications_for(db_session, "admin", "user.role_changed")
    assert len(outgoing) == 1
    out_data: Any = outgoing[0].payload["data"]
    assert out_data["removed"] == ["super_admin"]
    assert out_data["added"] == []
    assert out_data["actorUsername"] == "admin"

    incoming = await _notifications_for(db_session, "ra", "user.role_changed")
    assert len(incoming) == 1
    in_data: Any = incoming[0].payload["data"]
    assert in_data["added"] == ["super_admin"]
    assert in_data["removed"] == []
    assert in_data["actorUsername"] == "admin"


# ---------------------------------------------------------------------------
# user.deleted / user.restored
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R102")
@pytest.mark.asyncio
async def test_soft_delete_notifies_other_admins(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")
    _ = await create_member_user(db_session, "amy")

    response = await client.delete("/v1/users/by_id/amy", headers=auth(admin_token))
    assert response.status_code == 200

    # Both admins get the audit-style notification.
    for admin_name in ("admin", "ra"):
        rows = await _notifications_for(db_session, admin_name, "user.deleted")
        assert len(rows) == 1, f"{admin_name} should be notified"
        assert rows[0].payload["data"]["username"] == "amy"

    # The deleted user is not notified (FK + soft-deletion rules out the row).
    rows = await _notifications_for(db_session, "amy", "user.deleted")
    assert rows == []


@pytest.mark.requirement("notifications:R103")
@pytest.mark.asyncio
async def test_restore_notifies_user(client: AsyncClient, db_session: AsyncSession):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")

    _ = await client.delete("/v1/users/by_id/amy", headers=auth(admin_token))
    response = await client.post(
        "/v1/users/by_id/amy/restore", headers=auth(admin_token)
    )
    assert response.status_code == 200

    rows = await _notifications_for(db_session, "amy", "user.restored")
    assert len(rows) == 1
    assert rows[0].payload["data"]["username"] == "amy"
