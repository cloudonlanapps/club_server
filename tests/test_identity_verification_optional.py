"""Identity-document verification is optional per deployment (#428).

``IDENTITY_VERIFICATION_REQUIRED`` defaults on, which keeps the flow of #142:
a new user is ``registered``, uploads an identity document and submits for
review, and only then are admins told. Off, registration lands the user in
``pending`` and notifies admins at once, and submit-for-review — still the
way back after an admin's reconsider — checks no document.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import create_admin_user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, username: str = "newbie") -> dict:
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
    return response.json()


async def _login(client: AsyncClient, username: str = "newbie") -> str:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": "pw123"}
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


async def _pending_notifications(db_session: AsyncSession, admin: str) -> list:
    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.username == admin,
            Notification.type == "user.registration_pending",
        )
    )
    return list(result.scalars().all())


async def _status(client: AsyncClient, token: str) -> str:
    response = await client.get("/v1/auth/me", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["status"]


# ---------------------------------------------------------------------------
# Default: verification required, the #142 flow unchanged
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R1")
@pytest.mark.requirement("notifications:R97")
@pytest.mark.asyncio
async def test_should_register_as_registered_without_notifying_when_verification_is_on(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    await db_session.commit()

    user = await _register(client)

    assert user["status"] == "registered"
    assert await _status(client, await _login(client)) == "registered"
    assert await _pending_notifications(db_session, "admin") == []


@pytest.mark.asyncio
@pytest.mark.requirement("media:R77")
async def test_should_require_identity_document_on_submit_when_verification_is_on(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await _register(client)
    token = await _login(client)

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "IDENTITY_DOCUMENT_REQUIRED"
    assert await _status(client, token) == "registered"


# ---------------------------------------------------------------------------
# Off: straight to pending, admins told at registration
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R2")
@pytest.mark.asyncio
async def test_should_register_straight_to_pending_when_verification_is_off(
    client: AsyncClient, db_session: AsyncSession, identity_verification_off: None
):
    user = await _register(client)

    assert user["status"] == "pending"
    assert await _status(client, await _login(client)) == "pending"


@pytest.mark.requirement("users:R2")
@pytest.mark.requirement("notifications:R97")
@pytest.mark.asyncio
async def test_should_notify_admins_at_registration_when_verification_is_off(
    client: AsyncClient, db_session: AsyncSession, identity_verification_off: None
):
    _ = await create_admin_user(db_session)
    await db_session.commit()

    _ = await _register(client)

    rows = await _pending_notifications(db_session, "admin")
    assert len(rows) == 1
    assert rows[0].pending_action_type == "user_approval"
    assert rows[0].pending_action_key == "newbie"


@pytest.mark.requirement("users:R12")
@pytest.mark.asyncio
async def test_should_let_admin_approve_a_user_registered_while_verification_is_off(
    client: AsyncClient, db_session: AsyncSession, identity_verification_off: None
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    _ = await _register(client)

    response = await client.post("/v1/users/by_id/newbie/approve", headers=auth(admin))

    assert response.status_code == 200, response.text
    assert await _status(client, await _login(client)) == "active"


# ---------------------------------------------------------------------------
# Off: submit-for-review checks no document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R77")
async def test_should_submit_without_identity_document_when_verification_is_off(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    """A user left in ``registered`` when a deployment turns the check off."""
    from club_server.config import settings

    _ = await create_admin_user(db_session)
    await db_session.commit()
    _ = await _register(client)
    token = await _login(client)
    monkeypatch.setattr(settings, "identity_verification_required", False)

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert len(await _pending_notifications(db_session, "admin")) == 1


@pytest.mark.asyncio
async def test_should_resubmit_without_identity_document_after_reconsider_when_off(
    client: AsyncClient, db_session: AsyncSession, identity_verification_off: None
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    _ = await _register(client)
    token = await _login(client)
    sent_back = await client.post(
        "/v1/users/by_id/newbie/reconsider",
        json={"reason": "Phone number looks wrong"},
        headers=auth(admin),
    )
    assert sent_back.status_code == 201, sent_back.text
    assert await _status(client, token) == "registered"

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))

    assert response.status_code == 200, response.text
    assert await _status(client, token) == "pending"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@pytest.mark.requirement("platform:R14")
@pytest.mark.asyncio
async def test_should_report_identity_verification_on_in_capabilities_by_default(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await client.get("/v1/capabilities", headers=auth(admin))

    assert response.status_code == 200, response.text
    assert response.json()["identityVerification"] is True


@pytest.mark.requirement("platform:R14")
@pytest.mark.asyncio
async def test_should_report_identity_verification_off_in_capabilities_when_off(
    client: AsyncClient, db_session: AsyncSession, identity_verification_off: None
):
    admin = await create_admin_user(db_session)

    response = await client.get("/v1/capabilities", headers=auth(admin))

    assert response.status_code == 200, response.text
    assert response.json()["identityVerification"] is False
