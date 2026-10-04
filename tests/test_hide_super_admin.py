import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import User, UserStatus
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_regular_admin_user


async def create_regular_user(db_session: AsyncSession, username: str) -> None:
    """Create an active regular user directly in the database."""
    user = User(
        username=username,
        password=AuthService.hash_password("testpass123"),
        first_name=username.capitalize(),
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()


@pytest.mark.requirement("users:R60")
@pytest.mark.asyncio
async def test_list_users_excludes_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that GET /users does not include super admin accounts."""
    token = await create_admin_user(db_session)
    await create_regular_user(db_session, "alice")
    await create_regular_user(db_session, "bob")

    response = await client.get(
        "/v1/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    usernames = [u["username"] for u in data["items"]]
    assert "admin" not in usernames
    assert "alice" in usernames
    assert "bob" in usernames
    assert data["total"] == 2


@pytest.mark.requirement("users:R51")
@pytest.mark.requirement("users:R60")
@pytest.mark.asyncio
async def test_list_deleted_users_excludes_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that GET /users/deleted does not include soft-deleted super admin."""
    token = await create_admin_user(db_session)

    # Create a regular admin to use for querying after super admin is soft-deleted
    admin_token = await create_regular_admin_user(db_session)

    # Create and soft-delete a regular user
    await create_regular_user(db_session, "deleteduser")
    delete_response = await client.delete(
        "/v1/users/by_id/deleteduser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deletedAtUtc"] is not None

    # Soft-delete the super admin directly in DB
    from sqlalchemy import select as sa_select

    result = await db_session.execute(sa_select(User).where(User.username == "admin"))
    admin_user = result.scalar_one()
    admin_user.deleted_at = now_utc_ms()
    await db_session.flush()

    response = await client.get(
        "/v1/users/deleted",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    usernames = [u["username"] for u in data["items"]]
    assert "admin" not in usernames
    assert "deleteduser" in usernames
    assert data["total"] == 1


@pytest.mark.requirement("users:R47")
@pytest.mark.asyncio
async def test_get_user_by_username_returns_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that GET /users/{username} still returns super admin when queried directly."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/users/by_id/admin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    assert response.json()["isSuperAdmin"] is True
