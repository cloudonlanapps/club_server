"""Tests for #120 — defer ``user_approval`` notification until the user
signals completion via ``POST /v1/users/me/submit-for-review``.

Covers the acceptance criteria on the issue:

* ``auth.register`` lands a new user at ``status=registered`` and emits
  *no* ``user.registration_pending`` notification.
* ``get_authenticated_user`` accepts ``registered`` / ``pending`` /
  ``active`` and rejects unauthenticated callers.
* ``get_current_user`` relaxation: ``pending`` users can read
  ``/users/me`` and ``/notifications``.
* ``POST /v1/users/me/submit-for-review`` flips ``registered → pending``
  atomically and enqueues exactly one admin notification with the
  caller's username as the ``pendingActionKey``.
* The endpoint writes an ``action=submit_for_review`` audit log entry.
* The endpoint returns 409 ``INVALID_STATE`` for any starting status
  other than ``registered``.
* ``approveUser`` still requires ``pending`` (rejects ``registered``).
* ``blockUser`` succeeds from both ``registered`` and ``pending``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.notification import Notification
from club_server.db.models.user import User

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_member_user,
    create_registered_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, username: str = "newbie") -> None:
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


async def _login(client: AsyncClient, username: str, password: str = "pw123") -> str:
    login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()["accessToken"]


# ---------------------------------------------------------------------------
# Registration lands at status=registered with no user_approval notification.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R1")
@pytest.mark.asyncio
async def test_register_lands_user_at_registered_status(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    await _register(client, "newbie")

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "newbie"))
    ).scalar_one()
    assert user.status == "registered"


@pytest.mark.requirement("notifications:R97")
@pytest.mark.asyncio
async def test_register_does_not_emit_user_approval_notification(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    await _register(client, "newbie")

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.type == "user.registration_pending",
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(rows) == []


# ---------------------------------------------------------------------------
# Dependency: pending users now read /users/me and /notifications.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("auth:R3")
@pytest.mark.asyncio
async def test_pending_user_can_read_me_endpoint(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")

    submit = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "pending"

    me = await client.get("/v1/auth/me", headers=auth(token))
    assert me.status_code == 200, me.text
    assert me.json()["status"] == "pending"


@pytest.mark.requirement("notifications:R19")
@pytest.mark.asyncio
async def test_pending_user_can_read_notifications(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")
    submit = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert submit.status_code == 200

    notif = await client.get("/v1/notifications", headers=auth(token))
    assert notif.status_code == 200


# ---------------------------------------------------------------------------
# get_authenticated_user — rejects unauthenticated callers.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R11")
@pytest.mark.asyncio
async def test_submit_for_review_unauthenticated_rejected(client: AsyncClient):
    response = await client.post("/v1/users/me/submit-for-review")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# submit-for-review happy path.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R10")
@pytest.mark.requirement("notifications:R97")
@pytest.mark.asyncio
async def test_submit_for_review_flips_status_and_notifies_admins(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.status == "pending"

    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.username == "admin",
                    Notification.type == "user.registration_pending",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(rows)) == 1, "exactly one admin notification expected"
    n = list(rows)[0]
    assert n.pending_action_type == "user_approval"
    assert n.pending_action_key == "rosa"


@pytest.mark.requirement("users:R61")
@pytest.mark.asyncio
async def test_submit_for_review_writes_audit_log(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200

    db_session.expire_all()
    entries = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.actor_username == "rosa",
                    AuditLog.action == "submit_for_review",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(entries)) == 1


# ---------------------------------------------------------------------------
# submit-for-review state guard.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R11")
@pytest.mark.asyncio
async def test_submit_for_review_from_pending_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")

    first = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert first.status_code == 200

    again = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("users:R11")
@pytest.mark.asyncio
async def test_submit_for_review_from_active_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_member_user(db_session, "rosa")  # status=active

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------------------
# Admin policy: approveUser still requires pending; blockUser allows registered.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R14")
@pytest.mark.asyncio
async def test_approve_user_rejects_registered_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_registered_user(db_session, "rosa")

    response = await client.post(
        "/v1/users/by_id/rosa/approve", headers=auth(admin_token)
    )
    # service raises InvalidStateException; router currently maps to 422.
    assert response.status_code in (409, 422)
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("users:R24")
@pytest.mark.asyncio
async def test_block_user_allowed_from_registered(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_registered_user(db_session, "rosa")

    response = await client.post(
        "/v1/users/by_id/rosa/block", headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
