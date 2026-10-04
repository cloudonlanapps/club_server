"""Adding a requester directly clears the join-request notices (#524).

notifications R36a: when an admin adds a user who has a pending request to
join the group, singly or in bulk, the request reads approved and every
admin's actionable `group.join_request` notice is deleted, as approving the
request deletes it (R36).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import auth


async def _feed_types(client: AsyncClient, token: str) -> list[str]:
    response = await client.get("/v1/notifications", headers=auth(token))
    assert response.status_code == 200, response.text
    return [item["type"] for item in response.json()["items"]]


async def _pending_request(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, str, int]:
    """Super admin, admin, amy's token and a group amy has asked to join;
    both admins hold the join-request notice."""
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session, "ada")
    amy = await create_member_user(db_session, "amy")
    await db_session.commit()
    created = await client.post(
        "/v1/groups", json={"name": "G"}, headers=auth(super_admin)
    )
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    joined = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", json={"reason": "fun"}, headers=auth(amy)
    )
    assert joined.status_code == 201, joined.text
    for token in (super_admin, admin):
        assert await _feed_types(client, token) == ["group.join_request"]
    return super_admin, admin, amy, gid


async def _request_status(client: AsyncClient, amy: str) -> list[str]:
    response = await client.get("/v1/mygroups/by_id/amy/requests", headers=auth(amy))
    assert response.status_code == 200, response.text
    return [item["status"] for item in response.json()]


@pytest.mark.requirement("notifications:R36a")
@pytest.mark.asyncio
async def test_should_delete_join_request_notices_when_admin_adds_requester(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin, admin, amy, gid = await _pending_request(client, db_session)

    added = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin)
    )

    assert added.status_code == 201, added.text
    assert await _request_status(client, amy) == ["approved"]
    for token in (super_admin, admin):
        assert await _feed_types(client, token) == []
    pending = await client.get(
        "/v1/notifications/pending-actions", headers=auth(super_admin)
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["total"] == 0
    assert "group.member_added" in await _feed_types(client, amy)


@pytest.mark.requirement("notifications:R36a")
@pytest.mark.asyncio
async def test_should_delete_join_request_notices_when_admin_bulk_adds_requester(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin, admin, amy, gid = await _pending_request(client, db_session)

    added = await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["amy"]},
        headers=auth(super_admin),
    )

    assert added.status_code == 200, added.text
    assert added.json()["added"] == ["amy"]
    assert await _request_status(client, amy) == ["approved"]
    for token in (super_admin, admin):
        assert await _feed_types(client, token) == []
    assert "group.member_added" in await _feed_types(client, amy)
