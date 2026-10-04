"""Tests for #123 — surface ``adminReviewNote`` on ``UserPrivateResponse``.

The field is read-only and computed from the active
``user_review_requests`` row (see #122). It must appear on private
profile reads (``GET /v1/auth/me`` and
``GET /v1/users/by_id/{u}/private``) and must NOT appear on the public
``UserInfoResponse`` used by list / public detail endpoints.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus
from club_server.db.models.user_review_request import UserReviewRequest
from club_server.utils import now_utc_ms

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_registered_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_pending_user(
    db_session: AsyncSession, client: AsyncClient, username: str = "rosa"
) -> str:
    token = await create_registered_user(db_session, username)
    await attach_identity_document(db_session, username)
    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200, response.text
    return token


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_auth_me_returns_null_when_no_review_request_ever_created(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_registered_user(db_session, "alice")
    response = await client.get("/v1/auth/me", headers=auth(token))
    assert response.status_code == 200
    body = response.json()
    assert "adminReviewNote" in body
    assert body["adminReviewNote"] is None


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_auth_me_returns_reason_when_active_row_exists(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    user_token = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "Please re-upload your ID, the scan was blurry."},
    )
    assert response.status_code == 201

    response = await client.get("/v1/auth/me", headers=auth(user_token))
    assert response.status_code == 200
    assert (
        response.json()["adminReviewNote"]
        == "Please re-upload your ID, the scan was blurry."
    )


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_auth_me_returns_null_after_row_resolved(
    client: AsyncClient, db_session: AsyncSession
):
    """Resolved row should not surface — only active rows count."""
    admin_token = await create_admin_user(db_session)
    user_token = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "old request"},
    )
    assert response.status_code == 201

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(user_token),
        json={"firstName": "Rosa"},
    )
    assert response.status_code == 200

    response = await client.get("/v1/auth/me", headers=auth(user_token))
    assert response.status_code == 200
    assert response.json()["adminReviewNote"] is None


# ---------------------------------------------------------------------------
# /users/by_id/{u}/private (admin reads)
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_admin_private_view_surfaces_active_review_note(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "DOB looks wrong, please verify"},
    )
    assert response.status_code == 201

    response = await client.get(
        "/v1/users/by_id/rosa/private", headers=auth(admin_token)
    )
    assert response.status_code == 200
    assert response.json()["adminReviewNote"] == "DOB looks wrong, please verify"


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_admin_private_view_null_when_no_active_row(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_registered_user(db_session, "rosa")

    response = await client.get(
        "/v1/users/by_id/rosa/private", headers=auth(admin_token)
    )
    assert response.status_code == 200
    assert response.json()["adminReviewNote"] is None


# ---------------------------------------------------------------------------
# status-based short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_short_circuit_when_status_not_registered(
    client: AsyncClient, db_session: AsyncSession
):
    """When ``user.status != registered`` the field is ``None`` regardless
    of any (defensively-constructed) ``user_review_requests`` rows. The
    invariant prevents active rows in this state, but the short-circuit
    must hold even if one were inserted out of band.
    """
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)  # status=pending

    # Force-insert an active row bypassing service guardrails; the
    # short-circuit should still return null based on status alone.
    stale = UserReviewRequest(
        username="rosa",
        reason="should never surface",
        requested_by="admin",
        created_at=now_utc_ms(),
    )
    db_session.add(stale)
    await db_session.flush()

    response = await client.get(
        "/v1/users/by_id/rosa/private", headers=auth(admin_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == UserStatus.pending.value
    assert body["adminReviewNote"] is None


# ---------------------------------------------------------------------------
# Public / list endpoints MUST NOT leak the field
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_public_detail_endpoint_does_not_leak_review_note(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "private guidance"},
    )
    assert response.status_code == 201

    response = await client.get("/v1/users/by_id/rosa", headers=auth(admin_token))
    assert response.status_code == 200
    assert "adminReviewNote" not in response.json()


@pytest.mark.requirement("users:R18")
@pytest.mark.asyncio
async def test_list_endpoint_does_not_leak_review_note(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "private guidance"},
    )
    assert response.status_code == 201

    response = await client.get("/v1/users", headers=auth(admin_token))
    assert response.status_code == 200
    items = response.json()["items"]
    for item in items:
        assert "adminReviewNote" not in item
