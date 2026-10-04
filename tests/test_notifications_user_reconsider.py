"""Tests for #136 — ``user_reconsider_request`` notification is no longer
emitted.

The UI does not consume this notification (the recipient is forced to the
onboarding welcome screen and never reaches the pending-actions inbox), so
admin ``reconsider`` must not enqueue a row. The user-facing signal is
``UserPrivateResponse.adminReviewNote`` (#123); the admin-side
``user_approval`` notification is unaffected by this change.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification
from club_server.services.notification import (
    PENDING_ACTION_JOINS,
    NotificationService,
)

from .helpers import attach_identity_document, create_admin_user, create_registered_user


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


@pytest.mark.requirement("notifications:R38")
@pytest.mark.asyncio
async def test_reconsider_does_not_enqueue_notification(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "Please re-upload your ID"},
    )
    assert response.status_code == 201

    db_session.expire_all()
    notifications = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.pending_action_type == "user_reconsider_request",
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(notifications) == []


@pytest.mark.requirement("notifications:R38")
@pytest.mark.asyncio
async def test_reconsider_does_not_surface_in_pending_actions(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    r = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "please clarify"},
    )
    assert r.status_code == 201

    db_session.expire_all()
    page = await NotificationService(db_session).list_pending_actions(
        username="rosa",
        offset=0,
        limit=20,
    )
    assert page.items == []


def test_pending_action_registry_excludes_user_reconsider_request():
    assert "user_reconsider_request" not in PENDING_ACTION_JOINS
