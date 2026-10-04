"""The super-admin account is protected from other admins (#458, #459).

Block, mark-left and hard delete already refuse a super-admin target; these
tests cover the profile edit (which would let an admin redirect the account's
email and take it over through the public password reset) and soft delete.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user, create_regular_admin_user


@pytest.mark.requirement("users:R37")
@pytest.mark.asyncio
async def test_should_refuse_email_change_when_admin_edits_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """A regular admin cannot change the super admin's email (#458)."""
    super_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/admin",
        json={"email": "attacker@example.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_PROTECTION"

    check = await client.get(
        "/v1/users/by_id/admin/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert check.status_code == 200
    assert check.json()["email"] != "attacker@example.com"


@pytest.mark.requirement("users:R37")
@pytest.mark.asyncio
async def test_should_refuse_any_profile_edit_when_admin_edits_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """The refusal covers every field, not only the email (#458)."""
    super_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/admin",
        json={"firstName": "Renamed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_PROTECTION"

    check = await client.get(
        "/v1/users/by_id/admin/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert check.status_code == 200
    assert check.json()["firstName"] == "Admin"


@pytest.mark.requirement("users:R37")
@pytest.mark.asyncio
async def test_should_update_email_when_super_admin_edits_own_profile(
    client: AsyncClient, db_session: AsyncSession
):
    """The super admin can still edit their own profile (#458)."""
    super_token = await create_admin_user(db_session)

    response = await client.patch(
        "/v1/users/by_id/admin",
        json={"email": "owner@example.com"},
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert response.status_code == 200

    check = await client.get(
        "/v1/users/by_id/admin/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert check.status_code == 200
    assert check.json()["email"] == "owner@example.com"


@pytest.mark.requirement("users:R34")
@pytest.mark.asyncio
async def test_should_update_member_when_regular_admin_edits_member(
    client: AsyncClient, db_session: AsyncSession
):
    """A regular admin still edits ordinary users (#458)."""
    _ = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "plainmember")

    response = await client.patch(
        "/v1/users/by_id/plainmember",
        json={"email": "plain@example.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    check = await client.get(
        "/v1/users/by_id/plainmember/private",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert check.status_code == 200
    assert check.json()["email"] == "plain@example.com"


@pytest.mark.requirement("users:R59")
@pytest.mark.asyncio
async def test_should_refuse_soft_delete_when_target_is_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Soft delete refuses a super-admin target like the other destructive ops (#459)."""
    super_token = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    await db_session.commit()

    response = await client.delete(
        "/v1/users/by_id/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_PROTECTION"

    # The super admin's token still works: the account was not deleted.
    check = await client.get(
        "/v1/users/by_id/admin/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert check.status_code == 200
    assert check.json()["deletedAtUtc"] is None


@pytest.mark.requirement("users:R59")
@pytest.mark.asyncio
async def test_should_refuse_soft_delete_when_super_admin_deletes_self(
    client: AsyncClient, db_session: AsyncSession
):
    """The super admin cannot soft-delete their own account either (#459)."""
    super_token = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.delete(
        "/v1/users/by_id/admin",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_PROTECTION"

    check = await client.get(
        "/v1/users/by_id/admin/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert check.status_code == 200
    assert check.json()["deletedAtUtc"] is None


@pytest.mark.requirement("users:R50")
@pytest.mark.asyncio
async def test_should_soft_delete_member_when_regular_admin_deletes(
    client: AsyncClient, db_session: AsyncSession
):
    """Soft delete of an ordinary user still works (#459)."""
    _ = await create_admin_user(db_session)
    admin_token = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "goner")

    response = await client.delete(
        "/v1/users/by_id/goner",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["deletedAtUtc"] is not None

    listing = await client.get(
        "/v1/users/deleted",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert listing.status_code == 200
    assert "goner" in [u["username"] for u in listing.json()["items"]]
