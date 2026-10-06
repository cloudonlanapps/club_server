"""Tests for /mygroups/{username} membership listing and auth matrix."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .eligibility_helpers import with_group_band
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

DOB_2010 = 1262304000000
DOB_2014 = 1388534400000
ONE_DAY_MS = 86_400_000


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.requirement("groups:R46")
@pytest.mark.asyncio
async def test_mygroups_returns_manual_memberships(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    g = await client.post(
        "/v1/groups", json={"name": "Manual1"}, headers=auth(admin_token)
    )
    gid = g.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    response = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    assert response.status_code == 200
    names = [g["name"] for g in response.json()]
    assert "Manual1" in names


@pytest.mark.requirement("groups:R46")
@pytest.mark.asyncio
async def test_mygroups_returns_semi_auto_memberships(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="amy", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    g = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Semi1",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
                "semiAuto": True,
            }
        ),
        headers=auth(admin_token),
    )
    gid = g.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    response = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    assert response.status_code == 200
    names = [g["name"] for g in response.json()]
    assert "Semi1" in names


@pytest.mark.requirement("groups:R46")
@pytest.mark.asyncio
async def test_mygroups_returns_matching_auto_groups(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="amy", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Auto1",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
            }
        ),
        headers=auth(admin_token),
    )

    response = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    names = [g["name"] for g in response.json()]
    assert "Auto1" in names


@pytest.mark.asyncio
async def test_mygroups_excludes_non_matching_auto_groups(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="amy", date_of_birth=DOB_2010 - ONE_DAY_MS
    )
    await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Auto1",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
            }
        ),
        headers=auth(admin_token),
    )

    response = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    names = [g["name"] for g in response.json()]
    assert "Auto1" not in names


@pytest.mark.asyncio
async def test_mygroups_kind_field_present(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    response = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    for item in response.json():
        assert "kind" in item


@pytest.mark.asyncio
async def test_mygroups_self_access(client: AsyncClient, db_session: AsyncSession):
    _admin = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    response = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    assert response.status_code == 200


@pytest.mark.requirement("groups:R49")
@pytest.mark.asyncio
async def test_mygroups_other_member_forbidden(
    client: AsyncClient, db_session: AsyncSession
):
    _admin = await create_admin_user(db_session)
    a_token = await create_member_user(db_session, username="alice")
    _b_token = await create_member_user(db_session, username="bob")
    response = await client.get("/v1/mygroups/by_id/bob", headers=auth(a_token))
    assert response.status_code == 403


@pytest.mark.requirement("groups:R48")
@pytest.mark.asyncio
async def test_mygroups_admin_can_query_any(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, username="zoe")
    response = await client.get("/v1/mygroups/by_id/zoe", headers=auth(admin_token))
    assert response.status_code == 200


@pytest.mark.requirement("groups:R48")
@pytest.mark.asyncio
async def test_mygroups_coach_can_query_any(
    client: AsyncClient, db_session: AsyncSession
):
    _admin = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coachjoe")
    _ = await create_member_user(db_session, username="zoe")
    response = await client.get("/v1/mygroups/by_id/zoe", headers=auth(coach_token))
    assert response.status_code == 200


@pytest.mark.requirement("groups:R50")
@pytest.mark.asyncio
async def test_mygroups_anon_unauthorized(client: AsyncClient):
    response = await client.get("/v1/mygroups/by_id/anyone")
    assert response.status_code == 401


@pytest.mark.requirement("groups:R48")
@pytest.mark.asyncio
async def test_mygroups_regular_admin_can_query_any(
    client: AsyncClient, db_session: AsyncSession
):
    _admin = await create_admin_user(db_session)
    reg_admin_token = await create_regular_admin_user(db_session, username="ra")
    _ = await create_member_user(db_session, username="zoe")
    response = await client.get("/v1/mygroups/by_id/zoe", headers=auth(reg_admin_token))
    assert response.status_code == 200
