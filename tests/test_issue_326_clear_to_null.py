"""Optional venue and group fields must clear on an explicit null (#326).

The services gated assignment on `is not None`, which cannot tell "field
omitted" from "field explicitly set to null" — so a client could never empty
an address once set. The call returned 200 with the old value intact, giving
the caller no way to detect the no-op short of re-reading and comparing.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user


async def _create_venue(client: AsyncClient, token: str) -> int:
    response = await client.post(
        "/v1/venues",
        json={
            "name": "Clearable Rink",
            "address": "1 Ice Road",
            "description": "A description",
            "mapUri": "https://maps.example.com/rink",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["address", "description", "mapUri"])
async def test_venue_field_clears_on_explicit_null(
    client: AsyncClient, db_session: AsyncSession, field: str
):
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)
    auth = {"Authorization": f"Bearer {token}"}

    response = await client.patch(
        f"/v1/venues/by_id/{venue_id}", json={field: None}, headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()[field] is None

    # Verify through the read path, not just the write response.
    read_back = await client.get(f"/v1/venues/by_id/{venue_id}", headers=auth)
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()[field] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["address", "description", "mapUri"])
async def test_venue_field_survives_when_omitted(
    client: AsyncClient, db_session: AsyncSession, field: str
):
    """Omitting the field must still leave the stored value untouched."""
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)
    auth = {"Authorization": f"Bearer {token}"}
    before = (await client.get(f"/v1/venues/by_id/{venue_id}", headers=auth)).json()

    response = await client.patch(
        f"/v1/venues/by_id/{venue_id}", json={"name": "Renamed Rink"}, headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed Rink"
    assert response.json()[field] == before[field]


@pytest.mark.asyncio
async def test_group_description_clears_on_explicit_null(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    auth = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/v1/groups",
        json={"name": "U-14", "description": "A description"},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}", json={"description": None}, headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] is None

    read_back = await client.get(f"/v1/groups/by_id/{group_id}", headers=auth)
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["description"] is None


@pytest.mark.asyncio
async def test_group_description_survives_when_omitted(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    auth = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/v1/groups",
        json={"name": "U-16", "description": "Keep me"},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}", json={"name": "U-16 Renamed"}, headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "U-16 Renamed"
    assert response.json()["description"] == "Keep me"
