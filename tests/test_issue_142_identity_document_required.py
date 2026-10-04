"""Tests for #142 — ``POST /v1/users/me/submit-for-review`` must reject the
``registered → pending`` transition unless the caller has at least one live
``user_media`` link tagged ``identity_document``.

Covers:

* Negative — fresh ``registered`` user, no media at all → 422
  ``IDENTITY_DOCUMENT_REQUIRED``; status remains ``registered``.
* Negative — caller has a media link but under a different tag → 422.
* Negative — caller had a link, but the underlying media row was
  soft-deleted (``media.deleted_at`` set) → 422.
* Positive — caller has exactly one ``identity_document`` link → 200,
  status flips to ``pending``.
* Positive — caller has two ``identity_document`` links (max allowed
  per existing per-user-tag gallery cap) → 200.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.media import Media
from club_server.db.models.user import User
from club_server.utils import now_utc_ms

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_registered_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R77")
async def test_submit_for_review_without_any_media_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    """Repro from the issue: register → submit-for-review with empty gallery
    must be rejected."""
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    await db_session.commit()  # persist setup before the 422 path rolls back

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "IDENTITY_DOCUMENT_REQUIRED"

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.status == "registered", "status must not advance on rejection"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R77")
async def test_submit_for_review_with_wrong_tag_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    """A media link under a *different* tag must not satisfy the
    precondition."""
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa", tag="profile_photo")

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "IDENTITY_DOCUMENT_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R77")
async def test_submit_for_review_with_soft_deleted_media_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    """A link whose backing media row is soft-deleted must not count."""
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    media_uuid = await attach_identity_document(db_session, "rosa")

    media = (
        await db_session.execute(select(Media).where(Media.uuid == media_uuid))
    ).scalar_one()
    media.deleted_at = now_utc_ms()
    await db_session.flush()

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "IDENTITY_DOCUMENT_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R77")
async def test_submit_for_review_with_identity_document_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    """Happy path — one ``identity_document`` link is enough."""
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_submit_for_review_with_two_identity_documents_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    """Two ``identity_document`` links (front + back of an ID) — still
    accepted."""
    _ = await create_admin_user(db_session)
    token = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")

    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
