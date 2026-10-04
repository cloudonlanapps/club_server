"""An email already held by another active user is refused with 409 (#464).

Create has always answered 409 ``DUPLICATE_EMAIL``; the profile update and
the reapply resubmission hit the same unique index and answered 500.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_member_user,
    create_registered_user,
)

TAKEN = "taken@example.com"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _give_email(
    client: AsyncClient, admin: str, username: str, email: str
) -> None:
    response = await client.patch(
        f"/v1/users/by_id/{username}", json={"email": email}, headers=_auth(admin)
    )
    assert response.status_code == 200, response.text


async def _email_of(client: AsyncClient, admin: str, username: str) -> str | None:
    response = await client.get(
        f"/v1/users/by_id/{username}/private", headers=_auth(admin)
    )
    assert response.status_code == 200, response.text
    return response.json()["email"]


@pytest.mark.requirement("users:R6")
@pytest.mark.asyncio
async def test_should_return_409_when_admin_updates_user_to_taken_email(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "holder")
    _ = await create_member_user(db_session, "mover")
    await _give_email(client, admin, "holder", TAKEN)
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/mover", json={"email": TAKEN}, headers=_auth(admin)
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "DUPLICATE_EMAIL"

    assert await _email_of(client, admin, "mover") is None
    assert await _email_of(client, admin, "holder") == TAKEN


@pytest.mark.requirement("users:R6")
@pytest.mark.asyncio
async def test_should_return_409_when_member_updates_own_email_to_taken_email(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "holder")
    mover = await create_member_user(db_session, "mover")
    await _give_email(client, admin, "holder", TAKEN)
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/mover", json={"email": TAKEN}, headers=_auth(mover)
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "DUPLICATE_EMAIL"

    assert await _email_of(client, admin, "mover") is None


@pytest.mark.asyncio
async def test_should_update_email_when_address_is_free(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "holder")
    _ = await create_member_user(db_session, "mover")
    await _give_email(client, admin, "holder", TAKEN)

    response = await client.patch(
        "/v1/users/by_id/mover",
        json={"email": "free@example.com"},
        headers=_auth(admin),
    )
    assert response.status_code == 200, response.text
    assert await _email_of(client, admin, "mover") == "free@example.com"


async def _reconsidered(client: AsyncClient, db_session: AsyncSession) -> str:
    """``rosa`` sent back to ``registered`` with an active review request."""
    admin = await create_admin_user(db_session)
    rosa = await create_registered_user(db_session, "rosa")
    await attach_identity_document(db_session, "rosa")
    submitted = await client.post("/v1/users/me/submit-for-review", headers=_auth(rosa))
    assert submitted.status_code == 200, submitted.text
    reconsidered = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        json={"reason": "please fix the form"},
        headers=_auth(admin),
    )
    assert reconsidered.status_code == 201, reconsidered.text
    _ = await create_member_user(db_session, "holder")
    await _give_email(client, admin, "holder", TAKEN)
    return admin


@pytest.mark.requirement("users:R6")
@pytest.mark.asyncio
async def test_should_return_409_when_reapply_sends_taken_email(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _reconsidered(client, db_session)
    await db_session.commit()
    login = await client.post(
        "/v1/auth/login", json={"username": "rosa", "password": "regpass123"}
    )
    assert login.status_code == 200, login.text
    rosa = login.json()["accessToken"]

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply", json={"email": TAKEN}, headers=_auth(rosa)
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "DUPLICATE_EMAIL"

    assert await _email_of(client, admin, "rosa") != TAKEN


@pytest.mark.requirement("users:R19")
@pytest.mark.asyncio
async def test_should_reapply_when_email_is_free(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _reconsidered(client, db_session)
    login = await client.post(
        "/v1/auth/login", json={"username": "rosa", "password": "regpass123"}
    )
    assert login.status_code == 200, login.text
    rosa = login.json()["accessToken"]

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        json={"email": "rosa@example.com"},
        headers=_auth(rosa),
    )
    assert response.status_code == 200, response.text
    assert await _email_of(client, admin, "rosa") == "rosa@example.com"
