"""Tests for issue #97 — member-facing join-request group name visibility.

Covers:
- group_name on JoinRequestResponse (member-side and admin-side endpoints)
- requested flag on /mygroups/{u}/eligible (groups with pending requests included)
- new /mygroups/{u}/group/{group_id} endpoint
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_manual(client: AsyncClient, admin_token: str, name: str) -> int:
    g = await client.post("/v1/groups", json={"name": name}, headers=auth(admin_token))
    assert g.status_code == 201
    return g.json()["id"]


# =============================================================================
# group_name on JoinRequestResponse
# =============================================================================


@pytest.mark.asyncio
async def test_create_join_request_response_includes_group_name(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Falcons")

    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert create.status_code == 201
    body = create.json()
    assert body["groupId"] == gid
    assert body["groupName"] == "Falcons"


@pytest.mark.asyncio
async def test_user_requests_listing_includes_group_name(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Hawks")

    await client.post(f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token))

    res = await client.get(
        "/v1/mygroups/by_id/amy/requests", headers=auth(member_token)
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["groupId"] == gid
    assert items[0]["groupName"] == "Hawks"


@pytest.mark.asyncio
async def test_admin_group_requests_listing_includes_group_name(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Sharks")
    await client.post(f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token))

    res = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(admin_token)
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["groupName"] == "Sharks"


@pytest.mark.asyncio
async def test_cancel_response_includes_group_name(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Wolves")
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    res = await client.delete(
        f"/v1/mygroups/by_id/amy/requests/{rid}", headers=auth(member_token)
    )
    assert res.status_code == 200
    assert res.json()["groupName"] == "Wolves"


@pytest.mark.asyncio
async def test_approve_and_reject_responses_include_group_name(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    a_token = await create_member_user(db_session, username="amy")
    b_token = await create_member_user(db_session, username="bob")
    gid_a = await _make_manual(client, admin_token, "Tigers")
    gid_b = await _make_manual(client, admin_token, "Bears")

    ra = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid_a}", headers=auth(a_token)
    )
    rid_a = ra.json()["id"]
    rb = await client.post(
        f"/v1/mygroups/by_id/bob/join/{gid_b}", headers=auth(b_token)
    )
    rid_b = rb.json()["id"]

    appr = await client.post(
        f"/v1/groups/by_id/{gid_a}/requests/{rid_a}/approve",
        headers=auth(admin_token),
    )
    assert appr.status_code == 200
    assert appr.json()["groupName"] == "Tigers"

    rej = await client.post(
        f"/v1/groups/by_id/{gid_b}/requests/{rid_b}/reject",
        json={"reason": "no"},
        headers=auth(admin_token),
    )
    assert rej.status_code == 200
    assert rej.json()["groupName"] == "Bears"


# =============================================================================
# requested flag on /mygroups/{u}/eligible
# =============================================================================


@pytest.mark.asyncio
async def test_eligible_groups_excludes_groups_user_already_belongs_to(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Joined")
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )
    assert add.status_code in (200, 201)

    res = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    assert res.status_code == 200
    assert all(g["id"] != gid for g in res.json())


@pytest.mark.asyncio
async def test_eligible_groups_includes_requested_groups_with_flag_true(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid_req = await _make_manual(client, admin_token, "Requested")
    gid_open = await _make_manual(client, admin_token, "Open")

    await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid_req}", headers=auth(member_token)
    )

    res = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    assert res.status_code == 200
    by_id = {g["id"]: g for g in res.json()}
    assert gid_req in by_id, "requested group must appear in eligible list"
    assert by_id[gid_req]["requested"] is True
    assert by_id[gid_req]["name"] == "Requested"
    assert gid_open in by_id
    assert by_id[gid_open]["requested"] is False


@pytest.mark.asyncio
async def test_eligible_requested_flag_clears_after_approval(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Approving")
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    before = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    assert any(g["id"] == gid and g["requested"] is True for g in before.json())

    appr = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve",
        headers=auth(admin_token),
    )
    assert appr.status_code == 200

    after = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    assert all(g["id"] != gid for g in after.json()), (
        "approved group becomes a membership and drops out of eligible list"
    )

    mygroups = await client.get("/v1/mygroups/by_id/amy", headers=auth(member_token))
    assert any(g["id"] == gid for g in mygroups.json())


@pytest.mark.asyncio
async def test_eligible_requested_flag_clears_after_rejection(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Rejecting")
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    rej = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/reject",
        json={"reason": "no"},
        headers=auth(admin_token),
    )
    assert rej.status_code == 200

    res = await client.get(
        "/v1/mygroups/by_id/amy/eligible", headers=auth(member_token)
    )
    by_id = {g["id"]: g for g in res.json()}
    assert gid in by_id
    assert by_id[gid]["requested"] is False


# =============================================================================
# /mygroups/by_id/{username}/group/{group_id}
# =============================================================================


@pytest.mark.asyncio
async def test_get_my_group_works_when_user_has_pending_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "InFlight")
    await client.post(f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token))

    res = await client.get(
        f"/v1/mygroups/by_id/amy/group/{gid}", headers=auth(member_token)
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == gid
    assert body["name"] == "InFlight"
    assert body["requested"] is True


@pytest.mark.asyncio
async def test_get_my_group_works_when_user_is_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Mine")
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    res = await client.get(
        f"/v1/mygroups/by_id/amy/group/{gid}", headers=auth(member_token)
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Mine"
    assert res.json()["requested"] is False


@pytest.mark.asyncio
async def test_get_my_group_returns_404_when_no_relation(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "Stranger")

    res = await client.get(
        f"/v1/mygroups/by_id/amy/group/{gid}", headers=auth(member_token)
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "GROUP_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_my_group_returns_404_for_unknown_group(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")

    res = await client.get(
        "/v1/mygroups/by_id/amy/group/9999", headers=auth(member_token)
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "GROUP_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_my_group_forbidden_for_other_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    a_token = await create_member_user(db_session, username="amy")
    b_token = await create_member_user(db_session, username="bob")
    gid = await _make_manual(client, admin_token, "Theirs")
    await client.post(f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(a_token))

    res = await client.get(f"/v1/mygroups/by_id/amy/group/{gid}", headers=auth(b_token))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_my_group_allowed_for_admin_or_coach(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="kelly")
    reg_admin = await create_regular_admin_user(db_session, username="ralph")
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token, "AdminPeek")
    await client.post(f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token))

    for tok in (coach_token, reg_admin):
        res = await client.get(f"/v1/mygroups/by_id/amy/group/{gid}", headers=auth(tok))
        assert res.status_code == 200
        assert res.json()["name"] == "AdminPeek"
