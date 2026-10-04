"""Tests for /groups/{id}/eligible and /mygroups/{u}/eligible."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    attach_identity_document,
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


async def _register_active(
    client: AsyncClient,
    admin_token: str,
    username: str,
    db_session: AsyncSession,
    *,
    gender: str | None = None,
    date_of_birth: int | None = None,
) -> None:
    reg = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "testpass123",
            "firstName": username.capitalize(),
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert reg.status_code in (200, 201)
    pre = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    assert pre.status_code == 200
    await attach_identity_document(db_session, username)
    sub = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {pre.json()['accessToken']}"},
    )
    assert sub.status_code == 200, sub.text
    appr = await client.post(
        f"/v1/users/by_id/{username}/approve", headers=auth(admin_token)
    )
    assert appr.status_code in (200, 201, 204)
    update: dict = {}
    if gender is not None:
        update["gender"] = gender
    if date_of_birth is not None:
        update["dateOfBirthUtc"] = date_of_birth
    if update:
        patch = await client.patch(
            f"/v1/users/by_id/{username}", json=update, headers=auth(admin_token)
        )
        assert patch.status_code == 200


@pytest.mark.requirement("groups:R27")
@pytest.mark.asyncio
async def test_eligible_endpoint_for_manual_lists_active_users_minus_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await _register_active(client, admin_token, "u1", db_session)
    await _register_active(client, admin_token, "u2", db_session)
    await _register_active(client, admin_token, "u3", db_session)

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/u1", headers=auth(admin_token)
    )

    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    assert response.status_code == 200
    names = sorted(u["username"] for u in response.json())
    assert "u1" not in names
    assert "u2" in names and "u3" in names


@pytest.mark.requirement("groups:R30")
@pytest.mark.asyncio
async def test_eligible_endpoint_excludes_inactive_users(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await _register_active(client, admin_token, "active1", db_session)

    # Pending user (registered, not approved)
    reg = await client.post(
        "/v1/auth/register",
        json={
            "username": "pending1",
            "email": "pending1@example.com",
            "password": "testpass123",
            "firstName": "Pending",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert reg.status_code in (200, 201)

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    names = [u["username"] for u in response.json()]
    assert "active1" in names
    assert "pending1" not in names


@pytest.mark.requirement("groups:R26")
@pytest.mark.requirement("groups:R28")
@pytest.mark.asyncio
async def test_eligible_endpoint_for_semi_auto_includes_coaches_and_admins(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, username="raymond")
    _ = await create_coach_user(db_session, username="coachjoe")
    # ineligible plain member (DOB outside window)
    _ = await create_member_user(
        db_session, username="young", date_of_birth=DOB_2010 - ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(admin_token),
    )
    gid = g.json()["id"]
    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    names = [u["username"] for u in response.json()]
    assert "raymond" in names
    assert "coachjoe" in names
    assert "young" not in names


@pytest.mark.requirement("groups:R28")
@pytest.mark.asyncio
async def test_eligible_endpoint_for_semi_auto_excludes_ineligible_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(
        db_session, username="okmember", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    _ = await create_member_user(
        db_session, username="badmember", date_of_birth=DOB_2010 - ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(admin_token),
    )
    gid = g.json()["id"]
    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    names = [u["username"] for u in response.json()]
    assert "okmember" in names
    assert "badmember" not in names


@pytest.mark.requirement("groups:R32")
@pytest.mark.asyncio
async def test_eligible_endpoint_for_auto_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    g = await client.post(
        "/v1/groups",
        json={"name": "A", "gender": "male"},
        headers=auth(admin_token),
    )
    gid = g.json()["id"]
    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "AUTO_GROUP_NOT_JOINABLE"


@pytest.mark.requirement("groups:R31")
@pytest.mark.asyncio
async def test_eligible_endpoint_excludes_users_with_pending_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="aspirant")

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]

    # member submits a request via /mygroups
    req = await client.post(
        f"/v1/mygroups/by_id/aspirant/join/{gid}", headers=auth(member_token)
    )
    assert req.status_code == 201

    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    names = [u["username"] for u in response.json()]
    assert "aspirant" not in names


@pytest.mark.requirement("groups:R33")
@pytest.mark.asyncio
async def test_eligible_endpoint_404_for_unknown_group(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    response = await client.get(
        "/v1/groups/by_id/99999/eligible", headers=auth(admin_token)
    )
    assert response.status_code == 404


@pytest.mark.requirement("groups:R33")
@pytest.mark.asyncio
async def test_eligible_endpoint_404_for_soft_deleted_group(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    await client.delete(f"/v1/groups/by_id/{gid}", headers=auth(admin_token))

    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    assert response.status_code == 404


@pytest.mark.requirement("groups:R47")
@pytest.mark.asyncio
async def test_mygroups_eligible_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="amy", date_of_birth=DOB_2010 + ONE_DAY_MS
    )

    # one manual, one matching semi_auto, one auto, one already-joined
    m = await client.post("/v1/groups", json={"name": "M1"}, headers=auth(admin_token))
    s = await client.post(
        "/v1/groups",
        json={
            "name": "S1",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(admin_token),
    )
    a = await client.post(
        "/v1/groups",
        json={"name": "A1", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(admin_token),
    )
    joined = await client.post(
        "/v1/groups", json={"name": "J1"}, headers=auth(admin_token)
    )
    await client.post(
        f"/v1/groups/by_id/{joined.json()['id']}/members/byname/amy",
        headers=auth(admin_token),
    )

    response = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    assert response.status_code == 200
    names = {g["name"] for g in response.json()}
    assert "M1" in names
    assert "S1" in names
    assert "A1" not in names
    assert "J1" not in names

    # Suppress unused-var warnings
    _ = m, s, a


@pytest.mark.requirement("groups:R49")
@pytest.mark.asyncio
async def test_mygroups_eligible_self_only_for_member_role(
    client: AsyncClient, db_session: AsyncSession
):
    _admin = await create_admin_user(db_session)
    a_token = await create_member_user(db_session, username="alice")
    _b_token = await create_member_user(db_session, username="bob")

    response = await client.get(
        "/v1/mygroups/by_id/bob/eligible", headers=auth(a_token)
    )
    assert response.status_code == 403


@pytest.mark.requirement("groups:R26")
@pytest.mark.asyncio
async def test_eligible_endpoint_callable_by_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R26: coach (not just admin) can call /eligible."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coach1")
    await _register_active(client, admin_token, "u1", db_session)
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]

    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(coach_token)
    )
    assert response.status_code == 200


@pytest.mark.requirement("groups:R29")
@pytest.mark.asyncio
async def test_eligible_endpoint_excludes_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """R29: super-admin is never returned in the eligible list."""
    admin_token = await create_admin_user(
        db_session
    )  # creates "admin" w/ is_super_admin=1
    await _register_active(client, admin_token, "regular", db_session)

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    response = await client.get(
        f"/v1/groups/by_id/{gid}/eligible", headers=auth(admin_token)
    )
    names = [u["username"] for u in response.json()]
    assert "regular" in names
    assert "admin" not in names


@pytest.mark.requirement("groups:R48")
@pytest.mark.asyncio
async def test_mygroups_eligible_admin_can_query_any_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, username="zoe")

    response = await client.get(
        "/v1/mygroups/by_id/zoe/eligible", headers=auth(admin_token)
    )
    assert response.status_code == 200
