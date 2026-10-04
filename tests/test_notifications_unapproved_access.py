"""A registered or pending user reads the unread count, marks read and
keeps preferences (#516).

notifications R20a: the unread count, marking one or all notifications
read, and reading and changing the delivery preferences take the same gate
as the feed (R19): any logged-in user, approved or not. Each is checked
through the feed or the preferences read back.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .helpers import create_admin_user, create_user_with_status
from .redesign_helpers import auth

UNAPPROVED = [UserStatus.registered, UserStatus.pending]


async def _unapproved_with_notices(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
) -> str:
    """An unapproved user ``newbie`` holding two unread notices."""
    admin = await create_admin_user(db_session)
    token = await create_user_with_status(db_session, "newbie", status)
    await db_session.commit()
    for _ in range(2):
        created = await client.post(
            "/v1/notifications",
            json={
                "username": "newbie",
                "type": "custom.notice",
                "channel": "app",
                "payload": {"v": 1, "type": "custom.notice", "data": {}},
            },
            headers=auth(admin),
        )
        assert created.status_code == 201, created.text
    return token


async def _unread_ids(client: AsyncClient, token: str) -> list[int]:
    feed = await client.get("/v1/notifications?unreadOnly=true", headers=auth(token))
    assert feed.status_code == 200, feed.text
    return [item["id"] for item in feed.json()["items"]]


@pytest.mark.requirement("notifications:R20a")
@pytest.mark.parametrize("status", UNAPPROVED, ids=lambda s: s.value)
@pytest.mark.asyncio
async def test_should_read_unread_count_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    token = await _unapproved_with_notices(client, db_session, status)

    response = await client.get("/v1/notifications/unread-count", headers=auth(token))

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    assert len(await _unread_ids(client, token)) == 2


@pytest.mark.requirement("notifications:R20a")
@pytest.mark.parametrize("status", UNAPPROVED, ids=lambda s: s.value)
@pytest.mark.asyncio
async def test_should_mark_one_read_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    token = await _unapproved_with_notices(client, db_session, status)
    first, second = await _unread_ids(client, token)

    response = await client.post(
        f"/v1/notifications/by_id/{first}/read", headers=auth(token)
    )

    assert response.status_code == 204, response.text
    assert await _unread_ids(client, token) == [second]


@pytest.mark.requirement("notifications:R20a")
@pytest.mark.parametrize("status", UNAPPROVED, ids=lambda s: s.value)
@pytest.mark.asyncio
async def test_should_mark_all_read_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    token = await _unapproved_with_notices(client, db_session, status)

    response = await client.post("/v1/notifications/read-all", headers=auth(token))

    assert response.status_code == 204, response.text
    assert await _unread_ids(client, token) == []


@pytest.mark.requirement("notifications:R20a")
@pytest.mark.parametrize("status", UNAPPROVED, ids=lambda s: s.value)
@pytest.mark.asyncio
async def test_should_read_and_set_preferences_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    token = await create_user_with_status(db_session, "newbie", status)
    await db_session.commit()

    before = await client.get("/v1/notifications/preferences", headers=auth(token))
    assert before.status_code == 200, before.text
    assert before.json()["emailEnabled"] is True

    changed = await client.patch(
        "/v1/notifications/preferences",
        json={"emailEnabled": False, "smsEnabled": True},
        headers=auth(token),
    )

    assert changed.status_code == 200, changed.text
    after = await client.get("/v1/notifications/preferences", headers=auth(token))
    assert after.status_code == 200, after.text
    assert after.json() == {
        "emailEnabled": False,
        "pushEnabled": True,
        "smsEnabled": True,
    }


@pytest.mark.requirement("notifications:R20")
@pytest.mark.parametrize("status", UNAPPROVED, ids=lambda s: s.value)
@pytest.mark.asyncio
async def test_should_refuse_pending_actions_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    token = await create_user_with_status(db_session, "newbie", status)
    await db_session.commit()

    response = await client.get(
        "/v1/notifications/pending-actions", headers=auth(token)
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "ACCOUNT_NOT_ACTIVE"
