"""Tests for groups rules that had no proving test (#501).

Each test names the rule it proves in ``docs/groups_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .eligibility_helpers import with_group_band
from .helpers import create_admin_user, create_member_user, create_user_with_status

DOB_2010 = 1262304000000  # 2010-01-01 UTC midnight
DOB_2014 = 1388534400000  # 2014-01-01 UTC midnight
ONE_DAY_MS = 86_400_000


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_semi_auto(client: AsyncClient, admin_token: str, name: str) -> int:
    g = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": name,
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
                "semiAuto": True,
            }
        ),
        headers=auth(admin_token),
    )
    assert g.status_code == 201, g.json()
    assert g.json()["kind"] == "semi_auto"
    return g.json()["id"]


@pytest.mark.requirement("groups:R40")
@pytest.mark.asyncio
async def test_should_refuse_add_when_user_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_user_with_status(db_session, "gone", UserStatus.active, deleted=True)
    await db_session.commit()
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    assert g.status_code == 201
    gid = g.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/gone", headers=auth(admin_token)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=auth(admin_token)
    )
    assert members.status_code == 200
    assert members.json() == []


@pytest.mark.requirement("groups:R42")
@pytest.mark.asyncio
async def test_should_remove_member_when_group_is_semi_auto(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_member_user(
        db_session, username="amy", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    gid = await _make_semi_auto(client, admin_token, "S")
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )
    assert add.status_code == 201

    remove = await client.delete(
        f"/v1/groups/by_id/{gid}/members/amy", headers=auth(admin_token)
    )
    assert remove.status_code == 200
    assert remove.json()["memberCount"] == 0

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=auth(admin_token)
    )
    assert members.status_code == 200
    assert "amy" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R47a")
@pytest.mark.asyncio
async def test_should_omit_semi_auto_group_from_requestable_when_criteria_fail(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="young", date_of_birth=DOB_2010 - ONE_DAY_MS
    )
    manual = await client.post(
        "/v1/groups", json={"name": "Open"}, headers=auth(admin_token)
    )
    assert manual.status_code == 201
    await _make_semi_auto(client, admin_token, "Window")

    response = await client.get(
        "/v1/mygroups/by_id/young/eligible", headers=auth(member_token)
    )
    assert response.status_code == 200
    names = {g["name"] for g in response.json()}
    assert "Open" in names
    assert "Window" not in names


@pytest.mark.requirement("groups:R47b")
@pytest.mark.asyncio
async def test_should_list_group_flagged_requested_when_request_is_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    asked = await client.post(
        "/v1/groups", json={"name": "Asked"}, headers=auth(admin_token)
    )
    other = await client.post(
        "/v1/groups", json={"name": "Other"}, headers=auth(admin_token)
    )
    assert asked.status_code == 201 and other.status_code == 201
    req = await client.post(
        f"/v1/mygroups/by_id/amy/join/{asked.json()['id']}",
        headers=auth(member_token),
    )
    assert req.status_code == 201

    response = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    assert response.status_code == 200
    by_name = {g["name"]: g for g in response.json()}
    assert by_name["Asked"]["requested"] is True
    assert by_name["Other"]["requested"] is False


@pytest.mark.requirement("groups:R60")
@pytest.mark.asyncio
async def test_should_return_404_when_user_cancels_another_users_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    alice_token = await create_member_user(db_session, username="alice")
    bob_token = await create_member_user(db_session, username="bob")
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    create = await client.post(
        f"/v1/mygroups/by_id/alice/join/{gid}", headers=auth(alice_token)
    )
    assert create.status_code == 201
    rid = create.json()["id"]

    response = await client.delete(
        f"/v1/mygroups/by_id/bob/requests/{rid}", headers=auth(bob_token)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "JOIN_REQUEST_NOT_FOUND"

    listing = await client.get(
        "/v1/mygroups/by_id/alice/requests", headers=auth(alice_token)
    )
    assert listing.status_code == 200
    matched = next(r for r in listing.json() if r["id"] == rid)
    assert matched["status"] == "pending"


@pytest.mark.requirement("groups:R64")
@pytest.mark.asyncio
async def test_should_mark_rejected_with_decider_when_admin_rejects_without_reason(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(admin_token))
    gid = g.json()["id"]
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/reject", headers=auth(admin_token)
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"

    listing = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(admin_token)
    )
    assert listing.status_code == 200
    matched = next(r for r in listing.json() if r["id"] == rid)
    assert matched["status"] == "rejected"
    assert matched["decidedBy"] == "admin"
    assert matched["decidedAt"] is not None

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=auth(admin_token)
    )
    assert "amy" not in [m["membername"] for m in members.json()]
