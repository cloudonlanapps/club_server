"""Member venue reads require a login; deleted listing stays admin-only (venue R8–R11).

#387 opened the listing to anonymous callers; #307 reversed that decision:
the website reads venues only through ``/v1/public/venues``, and every
``/v1/venues`` read is for logged-in users. Listing *deleted* venues is an
administrative view and stays admin-only.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user, create_member_user


async def _seed_venues(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int]:
    """Two live venues and one soft-deleted; returns admin token and the deleted id."""
    admin = await create_admin_user(db_session)
    headers = {"Authorization": f"Bearer {admin}"}
    ids = []
    for name in ("Rink A", "Rink B", "Old Rink"):
        response = await client.post("/v1/venues", json={"name": name}, headers=headers)
        assert response.status_code == 201
        ids.append(response.json()["id"])
    deleted = await client.delete(f"/v1/venues/by_id/{ids[2]}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["id"] == ids[2]
    return admin, ids[2]


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R9")
async def test_should_list_live_venues_only_when_authenticated(
    client: AsyncClient, db_session: AsyncSession
):
    admin, deleted_id = await _seed_venues(client, db_session)

    response = await client.get(
        "/v1/venues", headers={"Authorization": f"Bearer {admin}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert sorted(v["name"] for v in data["items"]) == ["Rink A", "Rink B"]
    assert deleted_id not in {v["id"] for v in data["items"]}


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R9")
async def test_should_list_same_venues_when_authenticated(
    client: AsyncClient, db_session: AsyncSession
):
    """The role changes nothing about the listing: every role sees the same set."""
    admin, _ = await _seed_venues(client, db_session)
    coach = await create_coach_user(db_session)
    member = await create_member_user(db_session)
    await db_session.commit()

    baseline = (
        await client.get("/v1/venues", headers={"Authorization": f"Bearer {admin}"})
    ).json()
    for token in (coach, member):
        response = await client.get(
            "/v1/venues", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json() == baseline


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R8")
async def test_should_read_one_venue_when_authenticated(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _ = await _seed_venues(client, db_session)
    headers = {"Authorization": f"Bearer {admin}"}
    listed = (await client.get("/v1/venues", headers=headers)).json()["items"][0]

    response = await client.get(f"/v1/venues/by_id/{listed['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == listed["name"]


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R10")
async def test_should_refuse_deleted_listing_when_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    _, _ = await _seed_venues(client, db_session)

    response = await client.get("/v1/venues/deleted")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R10")
async def test_should_refuse_deleted_listing_when_not_admin(
    client: AsyncClient, db_session: AsyncSession
):
    _, _ = await _seed_venues(client, db_session)
    coach = await create_coach_user(db_session)
    member = await create_member_user(db_session)
    await db_session.commit()

    for token in (coach, member):
        response = await client.get(
            "/v1/venues/deleted", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R10")
async def test_should_list_deleted_venues_when_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin, deleted_id = await _seed_venues(client, db_session)

    response = await client.get(
        "/v1/venues/deleted", headers={"Authorization": f"Bearer {admin}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == deleted_id
    assert data["items"][0]["name"] == "Old Rink"
