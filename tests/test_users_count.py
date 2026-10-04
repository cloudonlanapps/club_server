"""Tests for ``GET /v1/users/count`` (#308)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_registered_user,
    create_user_with_status,
)

ALL_STATUSES = ("registered", "pending", "active", "blocked", "left")


async def _seed_one_of_each_status(db_session: AsyncSession) -> None:
    """Create exactly one non-super-admin user in each of the five statuses."""
    await create_registered_user(db_session, username="reg1")
    await create_user_with_status(db_session, "pend1", UserStatus.pending)
    await create_member_user(db_session, username="act1")
    await create_user_with_status(db_session, "blk1", UserStatus.blocked)
    await create_user_with_status(db_session, "left1", UserStatus.left)


@pytest.mark.requirement("users:R45")
@pytest.mark.asyncio
async def test_should_report_every_status_when_no_users_exist(
    client: AsyncClient, db_session: AsyncSession
):
    """All five buckets are present and zero when only the super admin exists."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data["byStatus"].keys()) == set(ALL_STATUSES)
    assert all(data["byStatus"][s] == 0 for s in ALL_STATUSES), data["byStatus"]
    assert data["total"] == 0


@pytest.mark.requirement("users:R45")
@pytest.mark.asyncio
async def test_should_count_users_per_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Each status bucket reports the number of users in that status."""
    token = await create_admin_user(db_session)
    await _seed_one_of_each_status(db_session)
    await create_member_user(db_session, username="act2")

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["byStatus"] == {
        "registered": 1,
        "pending": 1,
        "active": 2,
        "blocked": 1,
        "left": 1,
    }


@pytest.mark.requirement("users:R45")
@pytest.mark.asyncio
async def test_should_include_registered_users_in_total(
    client: AsyncClient, db_session: AsyncSession
):
    """``total`` counts every bucket, the not-yet-approved one included."""
    token = await create_admin_user(db_session)
    await _seed_one_of_each_status(db_session)

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["byStatus"]["registered"] == 1
    assert data["total"] == 5
    assert data["total"] == sum(data["byStatus"].values())


@pytest.mark.requirement("users:R45")
@pytest.mark.asyncio
async def test_should_exclude_soft_deleted_users_from_count(
    client: AsyncClient, db_session: AsyncSession
):
    """A soft-deleted user is counted in no bucket and not in the total."""
    token = await create_admin_user(db_session)
    await create_member_user(db_session, username="act1")
    await create_user_with_status(db_session, "gone", UserStatus.active, deleted=True)

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["byStatus"]["active"] == 1
    assert data["total"] == 1


@pytest.mark.requirement("users:R45")
@pytest.mark.requirement("users:R60")
@pytest.mark.asyncio
async def test_should_exclude_super_admins_from_count(
    client: AsyncClient, db_session: AsyncSession
):
    """Super admins stay hidden from the counts, as they are from the list (#103)."""
    token = await create_admin_user(db_session)
    await create_member_user(db_session, username="act1")
    await create_user_with_status(
        db_session, "hidden_root", UserStatus.active, is_super_admin=True
    )

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["byStatus"]["active"] == 1
    assert data["total"] == 1


@pytest.mark.requirement("users:R45")
@pytest.mark.asyncio
async def test_should_agree_with_user_list_total(
    client: AsyncClient, db_session: AsyncSession
):
    """The non-registered buckets sum to the total reported by ``GET /v1/users``.

    ``GET /v1/users`` omits registered users unless asked for them, so the
    count agrees with the list it summarises once that bucket is set aside.
    """
    token = await create_admin_user(db_session)
    await _seed_one_of_each_status(db_session)
    headers = {"Authorization": f"Bearer {token}"}

    count_response = await client.get("/v1/users/count", headers=headers)
    assert count_response.status_code == 200, count_response.text
    by_status = count_response.json()["byStatus"]

    list_response = await client.get("/v1/users", headers=headers)
    assert list_response.status_code == 200, list_response.text
    list_total = list_response.json()["total"]

    assert list_total == 4
    assert sum(by_status[s] for s in ALL_STATUSES if s != "registered") == list_total


@pytest.mark.requirement("users:R45")
@pytest.mark.asyncio
async def test_should_allow_coach_to_read_count(
    client: AsyncClient, db_session: AsyncSession
):
    """A coach may read the counts, matching access to ``GET /v1/users``."""
    await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coach1")
    await create_member_user(db_session, username="act1")

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200, response.text
    # The coach counts as an active user alongside the member.
    assert response.json()["byStatus"]["active"] == 2


@pytest.mark.requirement("users:R46")
@pytest.mark.asyncio
async def test_should_reject_count_for_plain_member(
    client: AsyncClient, db_session: AsyncSession
):
    """A member with no admin or coach role is refused."""
    await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="act1")

    response = await client.get(
        "/v1/users/count",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403, response.text


@pytest.mark.requirement("users:R46")
@pytest.mark.asyncio
async def test_should_reject_count_when_unauthenticated(client: AsyncClient):
    """An anonymous caller is refused."""
    response = await client.get("/v1/users/count")
    assert response.status_code == 401, response.text
