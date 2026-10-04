"""Tests for users rules that had no proving test (#495): access and reads.

Each test names the rule it proves in ``docs/users_requirements.md``.
Account states are in ``test_users_requirement_gaps.py``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import User, UserStatus
from club_server.utils import MS_PER_DAY, now_utc_ms

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_user_with_status,
)

MS_PER_YEAR = int(365.25 * MS_PER_DAY)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _born_years_ago(years: int) -> int:
    """A UTC-midnight date of birth ``years`` and a half years back."""
    dob = now_utc_ms() - years * MS_PER_YEAR - 180 * MS_PER_DAY
    return dob - dob % MS_PER_DAY


def _admin_operations(target: str) -> list[tuple[str, str, dict | None]]:
    base = f"/v1/users/by_id/{target}"
    return [
        ("POST", f"{base}/approve", None),
        ("POST", f"{base}/reconsider", {"reason": "Fix it"}),
        ("POST", f"{base}/block", None),
        ("POST", f"{base}/unblock", None),
        ("POST", f"{base}/mark-left", None),
        ("POST", f"{base}/reactivate", None),
        ("POST", f"{base}/restore", None),
        ("DELETE", base, None),
        ("POST", f"{base}/roles", {"role": "coach"}),
        ("DELETE", f"{base}/roles/coach", None),
        (
            "POST",
            "/v1/users",
            {
                "username": "sneaky",
                "passwordHash": "SecurePass123",
                "firstName": "Sneaky",
                "gender": "male",
                "dateOfBirthUtc": 946684800000,
                "phone": "9876543210",
            },
        ),
    ]


@pytest.mark.requirement("users:R32")
@pytest.mark.asyncio
async def test_should_refuse_account_administration_when_caller_is_coach_or_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    member = await create_member_user(db_session, "milo")
    _ = await create_member_user(db_session, "tess")
    await db_session.commit()

    for token in (coach, member):
        for method, url, body in _admin_operations("tess"):
            response = await client.request(method, url, json=body, headers=auth(token))
            assert response.status_code == 403, (method, url, response.text)

    tess = await client.get("/v1/users/by_id/tess", headers=auth(admin))
    assert tess.json()["status"] == "active"
    assert tess.json()["roles"] == []
    assert tess.json()["deletedAtUtc"] is None
    sneaky = await client.get("/v1/users/by_id/sneaky", headers=auth(admin))
    assert sneaky.status_code == 404


@pytest.mark.requirement("users:R32")
@pytest.mark.asyncio
async def test_should_refuse_account_administration_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "tess")
    await db_session.commit()

    for method, url, body in _admin_operations("tess"):
        response = await client.request(method, url, json=body)
        assert response.status_code == 401, (method, url, response.text)

    tess = await client.get("/v1/users/by_id/tess", headers=auth(admin))
    assert tess.json()["status"] == "active"


@pytest.mark.requirement("users:R33")
@pytest.mark.asyncio
async def test_should_update_own_profile_when_member_edits_it(
    client: AsyncClient, db_session: AsyncSession
):
    amy = await create_member_user(db_session, "amy")

    response = await client.patch(
        "/v1/users/by_id/amy",
        json={"nickname": "Ames", "bio": "Left wing", "phone": "5550001"},
        headers=auth(amy),
    )
    assert response.status_code == 200, response.text

    own = await client.get("/v1/users/by_id/amy/private", headers=auth(amy))
    assert own.status_code == 200, own.text
    assert own.json()["nickname"] == "Ames"
    assert own.json()["bio"] == "Left wing"
    assert own.json()["phone"] == "5550001"


@pytest.mark.requirement("users:R34a")
@pytest.mark.asyncio
async def test_should_refuse_profile_edit_when_coach_or_member_edits_another_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    member = await create_member_user(db_session, "milo")
    _ = await create_member_user(db_session, "tess")
    await db_session.commit()

    for token in (coach, member):
        response = await client.patch(
            "/v1/users/by_id/tess", json={"bio": "Hijacked"}, headers=auth(token)
        )
        assert response.status_code == 403, response.text

    tess = await client.get("/v1/users/by_id/tess/private", headers=auth(admin))
    assert tess.json()["bio"] is None


@pytest.mark.requirement("users:R36a")
@pytest.mark.asyncio
async def test_should_keep_fields_left_out_when_profile_is_edited(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    first = await client.patch(
        "/v1/users/by_id/amy",
        json={"nickname": "Ames", "phone": "5550001"},
        headers=auth(admin),
    )
    assert first.status_code == 200, first.text

    second = await client.patch(
        "/v1/users/by_id/amy", json={"bio": "Left wing"}, headers=auth(admin)
    )
    assert second.status_code == 200, second.text

    amy = await client.get("/v1/users/by_id/amy/private", headers=auth(admin))
    assert amy.json()["bio"] == "Left wing"
    assert amy.json()["nickname"] == "Ames"
    assert amy.json()["phone"] == "5550001"
    assert amy.json()["firstName"] == "Amy"


@pytest.mark.requirement("users:R43")
@pytest.mark.asyncio
async def test_should_filter_users_by_age_when_bounds_are_given(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "kid", date_of_birth=_born_years_ago(10))
    _ = await create_member_user(db_session, "teen", date_of_birth=_born_years_ago(16))
    _ = await create_member_user(db_session, "adult", date_of_birth=_born_years_ago(35))

    response = await client.get(
        "/v1/users", params={"minAge": 12, "maxAge": 20}, headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    assert [u["username"] for u in response.json()["items"]] == ["teen"]


@pytest.mark.requirement("users:R43")
@pytest.mark.asyncio
async def test_should_sort_users_when_sort_key_and_direction_are_given(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    for name in ("bea", "cal", "abe"):
        _ = await create_member_user(db_session, name)
    # Distinct creation times, in an order no name sort gives.
    rows = await db_session.execute(select(User).where(User.username != "admin"))
    for user in rows.scalars():
        user.created_at = {"bea": 1000, "cal": 2000, "abe": 3000}[user.username]
    await db_session.flush()

    by_name = await client.get(
        "/v1/users",
        params={"sortBy": "username", "descending": True},
        headers=auth(admin),
    )
    assert by_name.status_code == 200, by_name.text
    assert [u["username"] for u in by_name.json()["items"]] == ["cal", "bea", "abe"]

    by_first_name = await client.get(
        "/v1/users", params={"sortBy": "firstName"}, headers=auth(admin)
    )
    assert [u["username"] for u in by_first_name.json()["items"]] == [
        "abe",
        "bea",
        "cal",
    ]

    by_created = await client.get("/v1/users", headers=auth(admin))
    assert by_created.status_code == 200, by_created.text
    assert [u["username"] for u in by_created.json()["items"]] == ["bea", "cal", "abe"]


@pytest.mark.requirement("users:R44a")
@pytest.mark.asyncio
async def test_should_refuse_user_list_when_caller_is_member(
    client: AsyncClient, db_session: AsyncSession
):
    member = await create_member_user(db_session, "milo")

    response = await client.get("/v1/users", headers=auth(member))

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.requirement("users:R47a")
@pytest.mark.asyncio
async def test_should_return_basic_profile_when_member_reads_another_user(
    client: AsyncClient, db_session: AsyncSession
):
    member = await create_member_user(db_session, "milo")
    _ = await create_member_user(db_session, "tess")

    response = await client.get("/v1/users/by_id/tess", headers=auth(member))

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "tess"
    assert "email" not in response.json()


@pytest.mark.requirement("users:R48a")
@pytest.mark.asyncio
async def test_should_return_own_private_profile_when_member_reads_it(
    client: AsyncClient, db_session: AsyncSession
):
    member = await create_member_user(db_session, "milo", gender="male")

    response = await client.get("/v1/users/by_id/milo/private", headers=auth(member))

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "milo"
    assert response.json()["gender"] == "male"


@pytest.mark.requirement("users:R48a")
@pytest.mark.asyncio
async def test_should_refuse_private_profile_when_member_reads_another_user(
    client: AsyncClient, db_session: AsyncSession
):
    member = await create_member_user(db_session, "milo")
    _ = await create_member_user(db_session, "tess")

    response = await client.get("/v1/users/by_id/tess/private", headers=auth(member))

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.requirement("users:R49")
@pytest.mark.asyncio
async def test_should_list_a_users_groups_when_read_by_self_or_staff_only(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    tess = await create_member_user(db_session, "tess")
    milo = await create_member_user(db_session, "milo")
    group = await client.post("/v1/groups", json={"name": "U12"}, headers=auth(admin))
    assert group.status_code == 201, group.text
    added = await client.post(
        f"/v1/groups/by_id/{group.json()['id']}/members/byname/tess",
        headers=auth(admin),
    )
    assert added.status_code == 201, added.text
    await db_session.commit()

    for token in (tess, coach):
        response = await client.get("/v1/users/by_id/tess/groups", headers=auth(token))
        assert response.status_code == 200, response.text
        assert [g["name"] for g in response.json()] == ["U12"]

    refused = await client.get("/v1/users/by_id/tess/groups", headers=auth(milo))
    assert refused.status_code == 403, refused.text


@pytest.mark.requirement("users:R51a")
@pytest.mark.asyncio
async def test_should_refuse_deleted_users_list_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    coach = await create_coach_user(db_session, "cora")
    _ = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )

    response = await client.get("/v1/users/deleted", headers=auth(coach))

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"
