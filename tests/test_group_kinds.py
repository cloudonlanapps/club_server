"""Tests for group kind derivation, semi-auto guards, and DOB-bound truncation."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user

DOB_2010 = 1262304000000  # 2010-01-01 UTC midnight (ms)
DOB_2014 = 1388534400000  # 2014-01-01 UTC midnight
ONE_DAY_MS = 86_400_000


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _approve_user(
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
    assert reg.status_code in (200, 201), reg.json()
    pre = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    assert pre.status_code == 200, pre.text
    await attach_identity_document(db_session, username)
    sub = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {pre.json()['accessToken']}"},
    )
    assert sub.status_code == 200, sub.text
    appr = await client.post(
        f"/v1/users/by_id/{username}/approve", headers=auth(admin_token)
    )
    assert appr.status_code in (200, 201, 204), appr.json()
    update: dict = {}
    if gender is not None:
        update["gender"] = gender
    if date_of_birth is not None:
        update["dateOfBirthUtc"] = date_of_birth
    if update:
        patch = await client.patch(
            f"/v1/users/by_id/{username}", json=update, headers=auth(admin_token)
        )
        assert patch.status_code == 200, patch.json()


@pytest.mark.requirement("groups:R1")
@pytest.mark.asyncio
async def test_create_manual_when_no_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups", json={"name": "Just Manual"}, headers=auth(token)
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "manual"

    listed = await client.get("/v1/groups", headers=auth(token))
    assert listed.status_code == 200
    item = next(g for g in listed.json()["items"] if g["name"] == "Just Manual")
    assert item["kind"] == "manual"


@pytest.mark.requirement("groups:R2")
@pytest.mark.asyncio
async def test_create_auto_when_criteria_and_no_flag(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups",
        json={"name": "Auto", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(token),
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "auto"


@pytest.mark.requirement("groups:R3")
@pytest.mark.asyncio
async def test_create_semi_auto_when_flag_true(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups",
        json={
            "name": "Semi Auto",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "semi_auto"


@pytest.mark.asyncio
async def test_semi_auto_flag_ignored_when_no_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups", json={"name": "No-op flag", "semiAuto": True}, headers=auth(token)
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "manual"


@pytest.mark.requirement("groups:R11")
@pytest.mark.asyncio
async def test_dob_bounds_non_midnight_rejected_with_422(
    client: AsyncClient, db_session: AsyncSession
):
    """Per #100: group dob bounds must be at UTC midnight; non-midnight is 422."""
    token = await create_admin_user(db_session)
    odd_ms = DOB_2010 + 14 * 3600 * 1000 + 23 * 60 * 1000 + 55 * 1000
    response = await client.post(
        "/v1/groups",
        json={"name": "Truncated", "dobOnOrAfterUtc": odd_ms},
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dobOnOrAfterUtc"


@pytest.mark.requirement("groups:R70")
@pytest.mark.asyncio
async def test_inclusive_lower_bound(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "edge", db_session, date_of_birth=DOB_2010)

    create = await client.post(
        "/v1/groups",
        json={"name": "From 2010", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(token),
    )
    assert create.status_code == 201, create.json()
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert "edge" in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R71")
@pytest.mark.asyncio
async def test_inclusive_upper_bound(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "edge", db_session, date_of_birth=DOB_2014)

    create = await client.post(
        "/v1/groups",
        json={"name": "Up to 2014", "dobOnOrBeforeUtc": DOB_2014},
        headers=auth(token),
    )
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert "edge" in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R72")
@pytest.mark.asyncio
async def test_just_outside_lower_bound_excluded(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    # User DOB is at UTC midnight (per #100); the realistic "just outside" case
    # is the previous UTC day, i.e. DOB_2010 - 1 day.
    await _approve_user(
        client, token, "outside", db_session, date_of_birth=DOB_2010 - ONE_DAY_MS
    )

    create = await client.post(
        "/v1/groups",
        json={"name": "From 2010", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(token),
    )
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert "outside" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R73")
@pytest.mark.asyncio
async def test_just_outside_upper_bound_excluded(
    client: AsyncClient, db_session: AsyncSession
):
    """User born at midnight of the day AFTER the upper bound is excluded."""
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "outside", db_session, date_of_birth=DOB_2014 + ONE_DAY_MS
    )

    create = await client.post(
        "/v1/groups",
        json={"name": "Up to 2014", "dobOnOrBeforeUtc": DOB_2014},
        headers=auth(token),
    )
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert "outside" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R71")
@pytest.mark.asyncio
async def test_user_dob_at_upper_bound_midnight_included(
    client: AsyncClient, db_session: AsyncSession
):
    """User born at UTC midnight of the upper-bound day is included.

    Per #100 the user-DOB write contract forbids non-midnight values, so the
    only legal "born on the upper-bound day" value is the bound itself.
    This regression-tests the eligibility SQL clause `< dob + 1 day` against
    that single legal value.
    """
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "exact", db_session, date_of_birth=DOB_2014)

    create = await client.post(
        "/v1/groups",
        json={"name": "Up to 2014", "dobOnOrBeforeUtc": DOB_2014},
        headers=auth(token),
    )
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert "exact" in [m["membername"] for m in members.json()]


@pytest.mark.asyncio
async def test_user_dob_midday_rejected_at_write_with_422(
    client: AsyncClient, db_session: AsyncSession
):
    """Per #100: PATCH user with non-midnight dateOfBirthUtc returns 422."""
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "midday", db_session)

    midday = DOB_2014 + 14 * 3600 * 1000
    response = await client.patch(
        "/v1/users/by_id/midday",
        json={"dateOfBirthUtc": midday},
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"

    # Sanity: stored value is unchanged (still the seed midnight from _approve_user).
    create = await client.post(
        "/v1/groups",
        json={"name": "Up to 2014", "dobOnOrBeforeUtc": DOB_2014},
        headers=auth(token),
    )
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    # Seed DOB (2000-01-01 UTC midnight from _approve_user) is still within
    # dobOnOrBeforeUtc=2014-01-01 — the 422 PATCH did not mutate it.
    assert "midday" in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R74")
@pytest.mark.asyncio
async def test_user_with_null_dob_excluded_when_dob_criteria_set(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "nodob", db_session)  # no DOB set

    create = await client.post(
        "/v1/groups",
        json={"name": "From 2010", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(token),
    )
    group_id = create.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert "nodob" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R75")
@pytest.mark.asyncio
async def test_gender_filter_applied_for_auto_and_semi_auto(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "male1", db_session, gender="male")
    await _approve_user(client, token, "female1", db_session, gender="female")

    auto = await client.post(
        "/v1/groups", json={"name": "AutoMales", "gender": "male"}, headers=auth(token)
    )
    auto_id = auto.json()["id"]
    members = await client.get(
        f"/v1/groups/by_id/{auto_id}/members", headers=auth(token)
    )
    names = [m["membername"] for m in members.json()]
    assert "male1" in names and "female1" not in names

    semi = await client.post(
        "/v1/groups",
        json={"name": "SemiMales", "gender": "male", "semiAuto": True},
        headers=auth(token),
    )
    semi_id = semi.json()["id"]
    eligible = await client.get(
        f"/v1/groups/by_id/{semi_id}/eligible", headers=auth(token)
    )
    e_names = [u["username"] for u in eligible.json()]
    assert "male1" in e_names and "female1" not in e_names


@pytest.mark.requirement("groups:R8")
@pytest.mark.asyncio
async def test_manual_to_auto_blocked_when_members_exist(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "m1", db_session)

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(token))
    gid = g.json()["id"]
    await client.post(f"/v1/groups/by_id/{gid}/members/byname/m1", headers=auth(token))

    response = await client.patch(
        f"/v1/groups/by_id/{gid}", json={"gender": "male"}, headers=auth(token)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEMBERS_EXIST"


@pytest.mark.requirement("groups:R10")
@pytest.mark.asyncio
async def test_manual_to_semi_auto_succeeds_when_all_members_eligible(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "m1", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(token))
    gid = g.json()["id"]
    await client.post(f"/v1/groups/by_id/{gid}/members/byname/m1", headers=auth(token))

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["kind"] == "semi_auto"


@pytest.mark.requirement("groups:R9")
@pytest.mark.asyncio
async def test_manual_to_semi_auto_blocked_when_some_members_ineligible(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "ok", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    await _approve_user(
        client, token, "bad", db_session, date_of_birth=DOB_2010 - ONE_DAY_MS
    )

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(token))
    gid = g.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["ok", "bad"]},
        headers=auth(token),
    )

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["code"] == "MEMBERS_INELIGIBLE"
    assert "bad" in body["membernames"]


@pytest.mark.requirement("groups:R7")
@pytest.mark.asyncio
async def test_semi_auto_to_manual_keeps_members(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "m1", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/m1", headers=auth(token)
    )
    assert add.status_code == 201

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": None, "dobOnOrBeforeUtc": None, "gender": None},
        headers=auth(token),
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "manual"

    members = await client.get(f"/v1/groups/by_id/{gid}/members", headers=auth(token))
    assert "m1" in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R36")
@pytest.mark.asyncio
async def test_admin_added_to_semi_auto_regardless_of_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_regular_admin_user

    token = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, username="anotheradmin")

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/anotheradmin", headers=auth(token)
    )
    assert add.status_code == 201


@pytest.mark.requirement("groups:R36")
@pytest.mark.asyncio
async def test_coach_added_to_semi_auto_regardless_of_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, username="thecoach")

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/thecoach", headers=auth(token)
    )
    assert add.status_code == 201


@pytest.mark.requirement("groups:R37")
@pytest.mark.asyncio
async def test_member_blocked_from_semi_auto_when_ineligible(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "young", db_session, date_of_birth=DOB_2010 - ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/young", headers=auth(token)
    )
    assert add.status_code == 422
    assert add.json()["detail"]["code"] == "NOT_ELIGIBLE"


@pytest.mark.requirement("groups:R35")
@pytest.mark.asyncio
async def test_member_added_to_semi_auto_when_eligible(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "ok", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/ok", headers=auth(token)
    )
    assert add.status_code == 201


@pytest.mark.requirement("groups:R41")
@pytest.mark.asyncio
async def test_bulk_add_to_semi_auto_partitions_results(
    client: AsyncClient, db_session: AsyncSession
):
    from .helpers import create_coach_user

    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "ok1", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    await _approve_user(
        client, token, "ok2", db_session, date_of_birth=DOB_2010 + 2 * ONE_DAY_MS
    )
    await _approve_user(
        client, token, "bad", db_session, date_of_birth=DOB_2010 - ONE_DAY_MS
    )
    _ = await create_coach_user(db_session, username="thecoach2")

    g = await client.post(
        "/v1/groups",
        json={
            "name": "S",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    # pre-add ok1 so it lands in alreadyMembers
    await client.post(f"/v1/groups/by_id/{gid}/members/byname/ok1", headers=auth(token))

    response = await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["ok1", "ok2", "bad", "thecoach2", "ghost"]},
        headers=auth(token),
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert sorted(body["added"]) == ["ok2", "thecoach2"]
    assert body["alreadyMembers"] == ["ok1"]
    assert body["notFound"] == ["ghost"]
    assert body["notEligible"] == ["bad"]


# =============================================================================
# Gap-fill tests (R6, R11, R12, R77, R78)
# =============================================================================


@pytest.mark.requirement("groups:R5")
@pytest.mark.requirement("groups:R6")
@pytest.mark.asyncio
async def test_empty_manual_can_be_converted_to_semi_auto(
    client: AsyncClient, db_session: AsyncSession
):
    """R6: empty manual group can be converted to semi_auto (not just auto)."""
    token = await create_admin_user(db_session)
    g = await client.post("/v1/groups", json={"name": "Empty"}, headers=auth(token))
    gid = g.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "semi_auto"


@pytest.mark.requirement("groups:R11")
@pytest.mark.asyncio
async def test_dob_bounds_non_midnight_on_update_rejected_with_422(
    client: AsyncClient, db_session: AsyncSession
):
    """Per #100: PATCH with a non-midnight dob bound is rejected with 422."""
    token = await create_admin_user(db_session)
    g = await client.post(
        "/v1/groups",
        json={"name": "Auto", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(token),
    )
    gid = g.json()["id"]

    odd_ms = DOB_2014 + 14 * 3600 * 1000 + 23 * 60 * 1000 + 55 * 1000
    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrBeforeUtc": odd_ms},
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dobOnOrBeforeUtc"


@pytest.mark.requirement("groups:R12")
@pytest.mark.asyncio
async def test_inverted_dob_window_on_update_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """R12: inverted DOB window via PATCH is also rejected."""
    token = await create_admin_user(db_session)
    g = await client.post(
        "/v1/groups",
        json={"name": "Auto", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(token),
    )
    gid = g.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrBeforeUtc": DOB_2010 - ONE_DAY_MS},
        headers=auth(token),
    )
    assert response.status_code == 422


@pytest.mark.requirement("groups:R77")
@pytest.mark.asyncio
async def test_soft_deleted_user_excluded_from_auto_group(
    client: AsyncClient, db_session: AsyncSession
):
    """R77: a soft-deleted user is excluded from auto-group dynamic members."""
    from club_server.db.models.user import User
    from sqlalchemy import select as sa_select

    token = await create_admin_user(db_session)
    await _approve_user(client, token, "deleted_user", db_session, gender="male")
    await _approve_user(client, token, "live_user", db_session, gender="male")

    # Soft-delete one user directly via the session.
    rows = await db_session.execute(
        sa_select(User).where(User.username == "deleted_user")
    )
    user = rows.scalar_one()
    user.deleted_at = 1
    await db_session.commit()

    g = await client.post(
        "/v1/groups", json={"name": "Males", "gender": "male"}, headers=auth(token)
    )
    gid = g.json()["id"]
    members = await client.get(f"/v1/groups/by_id/{gid}/members", headers=auth(token))
    names = [m["membername"] for m in members.json()]
    assert "live_user" in names
    assert "deleted_user" not in names


@pytest.mark.requirement("groups:R78")
@pytest.mark.asyncio
async def test_super_admin_excluded_from_auto_group(
    client: AsyncClient, db_session: AsyncSession
):
    """R78: super-admin never appears in auto-group computed members."""
    from club_server.db.models.user import User
    from sqlalchemy import select as sa_select

    token = await create_admin_user(db_session)
    await _approve_user(client, token, "regular", db_session, gender="male")

    # Give the super-admin a matching gender to prove the exclusion is by
    # is_super_admin, not by missing gender.
    rows = await db_session.execute(sa_select(User).where(User.username == "admin"))
    admin = rows.scalar_one()
    admin.gender = "male"
    await db_session.commit()

    g = await client.post(
        "/v1/groups", json={"name": "Males", "gender": "male"}, headers=auth(token)
    )
    gid = g.json()["id"]
    members = await client.get(f"/v1/groups/by_id/{gid}/members", headers=auth(token))
    names = [m["membername"] for m in members.json()]
    assert "regular" in names
    assert "admin" not in names


# =============================================================================
# R81: super-admin is invisible to membership operations
# =============================================================================


@pytest.mark.requirement("groups:R81")
@pytest.mark.asyncio
async def test_super_admin_cannot_be_added_by_name(
    client: AsyncClient, db_session: AsyncSession
):
    """R81: adding super-admin by name returns 404 (super-admin invisible)."""
    token = await create_admin_user(db_session)
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(token))
    gid = g.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/admin", headers=auth(token)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("groups:R81")
@pytest.mark.asyncio
async def test_super_admin_in_bulk_lands_in_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """R81: bulk-add with a super-admin username puts it in `notFound`."""
    token = await create_admin_user(db_session)
    await _approve_user(client, token, "amy", db_session)

    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(token))
    gid = g.json()["id"]
    response = await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["admin", "amy"]},
        headers=auth(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == ["amy"]
    assert body["notFound"] == ["admin"]


@pytest.mark.requirement("groups:R81")
@pytest.mark.asyncio
async def test_super_admin_cannot_create_join_request(
    client: AsyncClient, db_session: AsyncSession
):
    """R81: a join request targeting super-admin's username returns 404."""
    token = await create_admin_user(db_session)
    g = await client.post("/v1/groups", json={"name": "M"}, headers=auth(token))
    gid = g.json()["id"]

    # Super-admin acts on their own username — check_mygroups_access allows
    # self/admin/coach/super-admin, so the auth layer passes; then the user
    # lookup inside the service excludes super-admins → 404.
    response = await client.post(
        f"/v1/mygroups/by_id/admin/join/{gid}", headers=auth(token)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("groups:R9a")
@pytest.mark.asyncio
async def test_issue_93_semi_auto_criteria_edit_rejects_when_member_becomes_ineligible(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 93: semi_auto criteria edit rejects when an approved member becomes ineligible."""
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "i174a", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "I174A",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    assert g.status_code == 201, g.json()
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/i174a", headers=auth(token)
    )
    assert add.status_code == 201

    # Narrow lower bound past the member's DOB → member becomes ineligible.
    new_lower = DOB_2010 + 30 * ONE_DAY_MS
    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": new_lower},
        headers=auth(token),
    )
    assert response.status_code == 422, response.json()
    body = response.json()["detail"]
    assert body["code"] == "MEMBERS_INELIGIBLE"
    assert "i174a" in body["membernames"]

    # Group state must be unchanged.
    fresh = await client.get(f"/v1/groups/by_id/{gid}", headers=auth(token))
    assert fresh.status_code == 200
    data = fresh.json()
    assert data["kind"] == "semi_auto"
    assert data["dobOnOrAfterUtc"] == DOB_2010
    assert data["dobOnOrBeforeUtc"] == DOB_2014


@pytest.mark.requirement("groups:R5a")
@pytest.mark.requirement("groups:R10a")
@pytest.mark.asyncio
async def test_issue_93_semi_auto_criteria_edit_succeeds_when_all_members_match(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 93: semi_auto criteria edit succeeds when all members still match the new criteria."""
    token = await create_admin_user(db_session)
    await _approve_user(
        client, token, "i174b", db_session, date_of_birth=DOB_2010 + 100 * ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "I174B",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/i174b", headers=auth(token)
    )
    assert add.status_code == 201

    # Narrow window but still include the member's DOB.
    new_lower = DOB_2010 + 50 * ONE_DAY_MS
    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": new_lower},
        headers=auth(token),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["kind"] == "semi_auto"
    assert response.json()["dobOnOrAfterUtc"] == new_lower


@pytest.mark.requirement("groups:R7")
@pytest.mark.asyncio
async def test_issue_93_semi_auto_to_manual_succeeds_without_eligibility_check(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 93: semi_auto to manual succeeds without eligibility check (manual has no rules)."""
    token = await create_admin_user(db_session)
    # DOB sits inside the original window — but we will clear all criteria,
    # so eligibility is moot regardless.
    await _approve_user(
        client, token, "i174c", db_session, date_of_birth=DOB_2010 + ONE_DAY_MS
    )

    g = await client.post(
        "/v1/groups",
        json={
            "name": "I174C",
            "dobOnOrAfterUtc": DOB_2010,
            "dobOnOrBeforeUtc": DOB_2014,
            "semiAuto": True,
        },
        headers=auth(token),
    )
    gid = g.json()["id"]
    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/i174c", headers=auth(token)
    )
    assert add.status_code == 201

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={
            "dobOnOrAfterUtc": None,
            "dobOnOrBeforeUtc": None,
            "gender": None,
            "semiAuto": False,
        },
        headers=auth(token),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["kind"] == "manual"

    members = await client.get(f"/v1/groups/by_id/{gid}/members", headers=auth(token))
    assert "i174c" in [m["membername"] for m in members.json()]
