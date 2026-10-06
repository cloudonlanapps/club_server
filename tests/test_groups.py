import pytest
from httpx import AsyncClient
from sqlalchemy import select as _sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog

from .eligibility_helpers import with_group_band
from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)


async def create_user_and_approve(
    client: AsyncClient, token: str, username: str, db_session: AsyncSession
) -> None:
    """Helper to create and approve a user."""
    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "testpass123",
            "firstName": "Test",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    _pre = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    await attach_identity_document(db_session, username)
    _ = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {_pre.json()['accessToken']}"},
    )
    _ = await client.post(
        f"/v1/users/by_id/{username}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.requirement("groups:R1")
@pytest.mark.asyncio
async def test_create_group(client: AsyncClient, db_session: AsyncSession):
    """Test creating a group."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/groups",
        json={"name": "Test Group", "description": "A test group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Group"
    assert data["description"] == "A test group"
    assert data["memberCount"] == 0


@pytest.mark.requirement("groups:R21")
@pytest.mark.asyncio
async def test_list_groups(client: AsyncClient, db_session: AsyncSession):
    """Test listing groups."""
    token = await create_admin_user(db_session)

    _ = await client.post(
        "/v1/groups",
        json={"name": "Group 1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/groups",
        json={"name": "Group 2"},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.requirement("groups:R22")
@pytest.mark.asyncio
async def test_get_group_detail(client: AsyncClient, db_session: AsyncSession):
    """Test getting group detail."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group", "description": "Test description"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Group"
    assert data["description"] == "Test description"
    assert data["members"] == []


@pytest.mark.asyncio
async def test_get_nonexistent_group(client: AsyncClient, db_session: AsyncSession):
    """Test getting a nonexistent group returns 404."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/groups/by_id/99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert "GROUP_NOT_FOUND" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R4")
@pytest.mark.asyncio
async def test_update_group(client: AsyncClient, db_session: AsyncSession):
    """Test updating a group."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Original Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json={"name": "Updated Name", "description": "New description"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["description"] == "New description"


@pytest.mark.requirement("groups:R13")
@pytest.mark.asyncio
async def test_soft_delete_group(client: AsyncClient, db_session: AsyncSession):
    """Test soft deleting a group."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "To Delete"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    delete_response = await client.delete(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deletedAtUtc"] is not None

    # Verify group is still accessible via GET but has deletedAtUtc set
    get_response = await client.get(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is not None


@pytest.mark.requirement("groups:R14")
@pytest.mark.asyncio
async def test_restore_group(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a soft-deleted group."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "To Restore"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    # Soft delete
    await client.delete(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Restore
    restore_response = await client.post(
        f"/v1/groups/by_id/{group_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["id"] == group_id
    assert restore_response.json()["deletedAtUtc"] is None

    # Verify group is visible again with no deletedAtUtc
    get_response = await client.get(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is None


@pytest.mark.requirement("groups:R14a")
@pytest.mark.asyncio
async def test_restore_group_not_deleted(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a group that is not deleted fails."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Active Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    restore_response = await client.post(
        f"/v1/groups/by_id/{group_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 422


@pytest.mark.requirement("groups:R16")
@pytest.mark.asyncio
async def test_hard_delete_group(client: AsyncClient, db_session: AsyncSession):
    """Test hard deleting a group (super admin)."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "To Wipeout"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    # Soft delete first (required before hard delete)
    await client.delete(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.delete(
        f"/v1/groups/by_id/{group_id}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Verify group is permanently gone
    restore_response = await client.post(
        f"/v1/groups/by_id/{group_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 404


@pytest.mark.requirement("groups:R15")
@pytest.mark.asyncio
async def test_hard_delete_group_requires_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard DELETE requires super admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Protected Group"},
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    group_id = create_response.json()["id"]

    # Regular admin should fail on hard delete
    delete_response = await client.delete(
        f"/v1/groups/by_id/{group_id}/hard",
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert delete_response.status_code == 403


@pytest.mark.requirement("groups:R34")
@pytest.mark.asyncio
async def test_add_member_to_group(client: AsyncClient, db_session: AsyncSession):
    """Test adding a member to a group."""
    token = await create_admin_user(db_session)
    _ = await create_user_and_approve(client, token, "testuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/testuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["membername"] == "testuser"


@pytest.mark.requirement("groups:R39")
@pytest.mark.asyncio
async def test_add_duplicate_member_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test adding the same member twice fails."""
    token = await create_admin_user(db_session)
    _ = await create_user_and_approve(client, token, "testuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    _ = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/testuser",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/testuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert "ALREADY_MEMBER" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R40")
@pytest.mark.asyncio
async def test_add_nonexistent_user_to_group_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test adding nonexistent user to group fails."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/nonexistent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert "USER_NOT_FOUND" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R42")
@pytest.mark.asyncio
async def test_remove_member_from_group(client: AsyncClient, db_session: AsyncSession):
    """Test removing a member from a group."""
    token = await create_admin_user(db_session)
    _ = await create_user_and_approve(client, token, "testuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    _ = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/testuser",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.delete(
        f"/v1/groups/by_id/{group_id}/members/testuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == group_id
    assert data["name"] == "Test Group"
    assert data["memberCount"] == 0

    members_response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert members_response.status_code == 200
    assert len(members_response.json()) == 0


@pytest.mark.requirement("groups:R44")
@pytest.mark.asyncio
async def test_remove_nonmember_fails(client: AsyncClient, db_session: AsyncSession):
    """Test removing user who is not a member fails."""
    token = await create_admin_user(db_session)
    _ = await create_user_and_approve(client, token, "testuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.delete(
        f"/v1/groups/by_id/{group_id}/members/testuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert "MEMBER_NOT_FOUND" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R22")
@pytest.mark.asyncio
async def test_list_group_members(client: AsyncClient, db_session: AsyncSession):
    """Test listing group members."""
    token = await create_admin_user(db_session)
    _ = await create_user_and_approve(client, token, "user1", db_session)
    _ = await create_user_and_approve(client, token, "user2", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    _ = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/user1",
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/user2",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    membernames = [m["membername"] for m in data]
    assert "user1" in membernames
    assert "user2" in membernames


@pytest.mark.requirement("groups:R25")
@pytest.mark.asyncio
async def test_groups_without_auth_fails(client: AsyncClient):
    """Test that groups endpoints require authentication."""
    response = await client.get("/v1/groups")
    assert response.status_code == 401

    response = await client.post(
        "/v1/groups",
        json={"name": "Test"},
    )
    assert response.status_code == 401


@pytest.mark.requirement("groups:R18")
@pytest.mark.requirement("groups:R19")
@pytest.mark.asyncio
async def test_soft_deleted_group_excluded_from_list(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that soft-deleted groups don't appear in the active list but appear in deleted list."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Group to List-Delete"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    # Soft delete
    await client.delete(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Not in active list
    list_response = await client.get(
        "/v1/groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    group_ids = [g["id"] for g in list_response.json()["items"]]
    assert group_id not in group_ids

    # In deleted list with deletedAtUtc set
    deleted_response = await client.get(
        "/v1/groups/deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    deleted_items = deleted_response.json()["items"]
    deleted_ids = [g["id"] for g in deleted_items]
    assert group_id in deleted_ids
    deleted_group = next(g for g in deleted_items if g["id"] == group_id)
    assert deleted_group["deletedAtUtc"] is not None


@pytest.mark.asyncio
async def test_bulk_add_members(client: AsyncClient, db_session: AsyncSession):
    """Test bulk adding members to a group."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "bulkuser1", db_session)
    await create_user_and_approve(client, token, "bulkuser2", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Bulk Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/bulk",
        json={"membernames": ["bulkuser1", "bulkuser2"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert sorted(data["added"]) == ["bulkuser1", "bulkuser2"]
    assert data["alreadyMembers"] == []
    assert data["notFound"] == []

    # Verify members appear in member list
    members_response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(members_response.json()) == 2


@pytest.mark.requirement("groups:R41")
@pytest.mark.asyncio
async def test_bulk_add_members_with_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """Test bulk add with mix of existing and nonexistent users."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "realuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Bulk Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/bulk",
        json={"membernames": ["realuser", "fakeuser"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["added"] == ["realuser"]
    assert data["notFound"] == ["fakeuser"]


@pytest.mark.requirement("groups:R41")
@pytest.mark.asyncio
async def test_bulk_add_members_already_member(
    client: AsyncClient, db_session: AsyncSession
):
    """Test bulk add with a user already in the group."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "existinguser", db_session)
    await create_user_and_approve(client, token, "newuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Bulk Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    # Add one user first via single-add
    await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/existinguser",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Bulk add both
    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/bulk",
        json={"membernames": ["existinguser", "newuser"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["added"] == ["newuser"]
    assert data["alreadyMembers"] == ["existinguser"]


# =============================================================================
# Auto Group Tests (DOB-window criteria, kind-based)
# =============================================================================

# Reference DOBs for deterministic tests.
DOB_2010 = 1262304000000  # 2010-01-01 UTC midnight
DOB_2014 = 1388534400000  # 2014-01-01 UTC midnight
DOB_2000 = 946684800000  # 2000-01-01 UTC midnight


async def create_user_with_profile(
    client: AsyncClient,
    token: str,
    username: str,
    db_session: AsyncSession,
    gender: str | None = None,
    date_of_birth: int | None = None,
) -> None:
    """Create, approve, and set profile fields on a user."""
    await create_user_and_approve(client, token, username, db_session)
    update: dict = {}
    if gender is not None:
        update["gender"] = gender
    if date_of_birth is not None:
        update["dateOfBirthUtc"] = date_of_birth
    if update:
        await client.patch(
            f"/v1/users/by_id/{username}",
            json=update,
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.requirement("groups:R2")
@pytest.mark.asyncio
async def test_create_auto_group_with_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    """A group with DOB-window or gender criteria defaults to kind=auto."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Boys 2010-2014",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
                "gender": "male",
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["kind"] == "auto"
    assert data["gender"] == "male"
    assert data["dobOnOrAfterUtc"] == DOB_2010
    assert data["dobOnOrBeforeUtc"] == DOB_2014


@pytest.mark.requirement("groups:R1")
@pytest.mark.asyncio
async def test_create_manual_group_no_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    """A group without criteria fields is manual, regardless of the semiAuto flag."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/groups",
        json={"name": "Manual Group", "semiAuto": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "manual"


@pytest.mark.requirement("groups:R12")
@pytest.mark.asyncio
async def test_dob_bounds_must_not_be_inverted(
    client: AsyncClient, db_session: AsyncSession
):
    """dobOnOrAfterUtc later than dobOnOrBeforeUtc is rejected."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Bad Window",
                "dobOnOrAfterUtc": DOB_2014,
                "dobOnOrBeforeUtc": DOB_2010,
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.requirement("groups:R38")
@pytest.mark.asyncio
async def test_auto_group_rejects_add_member(
    client: AsyncClient, db_session: AsyncSession
):
    """Adding a member to an auto group is rejected."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "someuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Auto Group", "gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/someuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "AUTO_GROUP_MODIFICATION_NOT_ALLOWED" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R38")
@pytest.mark.asyncio
async def test_auto_group_rejects_bulk_add(
    client: AsyncClient, db_session: AsyncSession
):
    """Bulk-adding members to an auto group is rejected."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json=with_group_band({"name": "Auto Group", "dobOnOrAfterUtc": DOB_2010}),
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/bulk",
        json={"membernames": ["anyone"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "AUTO_GROUP_MODIFICATION_NOT_ALLOWED" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R43")
@pytest.mark.asyncio
async def test_auto_group_rejects_remove_member(
    client: AsyncClient, db_session: AsyncSession
):
    """Removing a member from an auto group is rejected."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Auto Group", "gender": "female"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.delete(
        f"/v1/groups/by_id/{group_id}/members/someuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "AUTO_GROUP_MODIFICATION_NOT_ALLOWED" in str(response.json()["detail"])


@pytest.mark.requirement("groups:R23")
@pytest.mark.asyncio
async def test_auto_group_dynamic_members_by_gender(
    client: AsyncClient, db_session: AsyncSession
):
    """Auto group filters members by gender."""
    token = await create_admin_user(db_session)
    await create_user_with_profile(client, token, "maleuser", db_session, gender="male")
    await create_user_with_profile(
        client, token, "femaleuser", db_session, gender="female"
    )

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Males Only", "gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    members = response.json()
    membernames = [m["membername"] for m in members]
    assert "maleuser" in membernames
    assert "femaleuser" not in membernames


@pytest.mark.asyncio
async def test_auto_group_dynamic_members_by_dob_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Auto group filters members by DOB window."""
    token = await create_admin_user(db_session)
    await create_user_with_profile(
        client,
        token,
        "born2012",
        db_session,
        date_of_birth=DOB_2010 + 2 * 365 * 86400000,
    )
    await create_user_with_profile(
        client,
        token,
        "born2008",
        db_session,
        date_of_birth=DOB_2010 - 2 * 365 * 86400000,
    )
    await create_user_with_profile(
        client, token, "born2000", db_session, date_of_birth=DOB_2000
    )

    create_response = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Born 2010-2014",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    members = response.json()
    membernames = [m["membername"] for m in members]
    assert "born2012" in membernames
    assert "born2008" not in membernames
    assert "born2000" not in membernames


@pytest.mark.asyncio
async def test_auto_group_dynamic_members_combined(
    client: AsyncClient, db_session: AsyncSession
):
    """Auto group with both gender and DOB window filters."""
    token = await create_admin_user(db_session)
    await create_user_with_profile(
        client,
        token,
        "boy2012",
        db_session,
        gender="male",
        date_of_birth=DOB_2010 + 2 * 365 * 86400000,
    )
    await create_user_with_profile(
        client,
        token,
        "girl2012",
        db_session,
        gender="female",
        date_of_birth=DOB_2010 + 2 * 365 * 86400000,
    )
    await create_user_with_profile(
        client,
        token,
        "boy2000",
        db_session,
        gender="male",
        date_of_birth=DOB_2000,
    )

    create_response = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Boys 2010-2014",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
                "gender": "male",
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    members = response.json()
    membernames = [m["membername"] for m in members]
    assert "boy2012" in membernames
    assert "girl2012" not in membernames
    assert "boy2000" not in membernames


@pytest.mark.requirement("groups:R76")
@pytest.mark.asyncio
async def test_auto_group_excludes_non_active_users(
    client: AsyncClient, db_session: AsyncSession
):
    """Auto group only includes active users."""
    token = await create_admin_user(db_session)
    await create_user_with_profile(
        client, token, "activeuser", db_session, gender="male"
    )

    await client.post(
        "/v1/auth/register",
        json={
            "username": "pendinguser",
            "email": "pendinguser@example.com",
            "password": "testpass123",
            "firstName": "Pending",
            "gender": "male",
            "dateOfBirthUtc": DOB_2000,
            "phone": "1234567890",
        },
    )
    await client.patch(
        "/v1/users/by_id/pendinguser",
        json={"gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Males", "gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    members = response.json()
    membernames = [m["membername"] for m in members]
    assert "activeuser" in membernames
    assert "pendinguser" not in membernames


@pytest.mark.requirement("groups:R23")
@pytest.mark.asyncio
async def test_auto_group_member_count_in_list(
    client: AsyncClient, db_session: AsyncSession
):
    """Auto group memberCount reflects dynamic count in the list response."""
    token = await create_admin_user(db_session)
    await create_user_with_profile(client, token, "male1", db_session, gender="male")
    await create_user_with_profile(client, token, "male2", db_session, gender="male")

    await client.post(
        "/v1/groups",
        json={"name": "Males", "gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    groups = response.json()["items"]
    auto_group = next(g for g in groups if g["name"] == "Males")
    assert auto_group["memberCount"] == 2


@pytest.mark.requirement("groups:R5")
@pytest.mark.asyncio
async def test_update_auto_group_dob_window(
    client: AsyncClient, db_session: AsyncSession
):
    """Updating DOB window changes auto-group membership."""
    token = await create_admin_user(db_session)
    await create_user_with_profile(
        client,
        token,
        "born2012",
        db_session,
        date_of_birth=DOB_2010 + 2 * 365 * 86400000,
    )

    create_response = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Born 2010-2014",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]

    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "born2012" in [m["membername"] for m in members.json()]

    await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json=with_group_band(
            {
                "dobOnOrAfterUtc": DOB_2014,
                "dobOnOrBeforeUtc": DOB_2014 + 365 * 86400000,
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )

    members = await client.get(
        f"/v1/groups/by_id/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "born2012" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R7")
@pytest.mark.asyncio
async def test_clear_dob_criteria_makes_group_manual(
    client: AsyncClient, db_session: AsyncSession
):
    """Clearing all criteria converts an auto group back to manual."""
    token = await create_admin_user(db_session)

    create_response = await client.post(
        "/v1/groups",
        json=with_group_band(
            {
                "name": "Age Group",
                "dobOnOrAfterUtc": DOB_2010,
                "dobOnOrBeforeUtc": DOB_2014,
            }
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]
    assert create_response.json()["kind"] == "auto"

    update_response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json=with_group_band({"dobOnOrAfterUtc": None, "dobOnOrBeforeUtc": None}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["kind"] == "manual"
    assert update_response.json()["dobOnOrAfterUtc"] is None
    assert update_response.json()["dobOnOrBeforeUtc"] is None


@pytest.mark.asyncio
async def test_manual_group_unaffected(client: AsyncClient, db_session: AsyncSession):
    """Manual groups still work for add/remove."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "manualuser", db_session)

    create_response = await client.post(
        "/v1/groups",
        json={"name": "Manual Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_response.json()["id"]
    assert create_response.json()["kind"] == "manual"

    add_response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/manualuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert add_response.status_code == 201

    remove_response = await client.delete(
        f"/v1/groups/by_id/{group_id}/members/manualuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["memberCount"] == 0


# =============================================================================
# Manual-to-auto conversion guard
# =============================================================================


@pytest.mark.requirement("groups:R8")
@pytest.mark.asyncio
async def test_update_manual_group_with_members_to_auto_rejected_gender(
    client: AsyncClient, db_session: AsyncSession
):
    """Setting gender on a manual group with members returns 422 MEMBERS_EXIST."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "member1", db_session)

    create_resp = await client.post(
        "/v1/groups",
        json={"name": "Manual Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_resp.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/member1",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json={"gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEMBERS_EXIST"

    get_resp = await client.get(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.json()["kind"] == "manual"
    assert get_resp.json()["gender"] is None


@pytest.mark.requirement("groups:R8")
@pytest.mark.asyncio
async def test_update_manual_group_with_members_to_auto_rejected_dob(
    client: AsyncClient, db_session: AsyncSession
):
    """Setting a DOB bound on a manual group with members returns 422 MEMBERS_EXIST."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "member1", db_session)

    create_resp = await client.post(
        "/v1/groups",
        json={"name": "Manual Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_resp.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/member1",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json=with_group_band({"dobOnOrAfterUtc": DOB_2010}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEMBERS_EXIST"


@pytest.mark.requirement("groups:R5")
@pytest.mark.requirement("groups:R6")
@pytest.mark.asyncio
async def test_update_manual_group_no_members_to_auto_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    """Empty manual group can be converted to auto by setting criteria."""
    token = await create_admin_user(db_session)

    create_resp = await client.post(
        "/v1/groups",
        json={"name": "Empty Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_resp.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json={"gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "auto"


@pytest.mark.asyncio
async def test_update_auto_group_criteria_change_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    """An already-auto group can freely change its criteria."""
    token = await create_admin_user(db_session)

    create_resp = await client.post(
        "/v1/groups",
        json={"name": "Auto Group", "gender": "male"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_resp.json()["id"]
    assert create_resp.json()["kind"] == "auto"

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json=with_group_band({"dobOnOrAfterUtc": DOB_2010}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "auto"


@pytest.mark.requirement("groups:R4")
@pytest.mark.asyncio
async def test_update_manual_group_with_members_non_criteria_allowed(
    client: AsyncClient, db_session: AsyncSession
):
    """Non-criteria updates (name, description) work on manual groups with members."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "member1", db_session)

    create_resp = await client.post(
        "/v1/groups",
        json={"name": "Manual Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_resp.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/member1",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json={"name": "Renamed Group", "description": "Updated"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Group"


@pytest.mark.asyncio
async def test_update_manual_group_remove_members_then_set_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    """After removing all members, setting criteria is allowed."""
    token = await create_admin_user(db_session)
    await create_user_and_approve(client, token, "member1", db_session)

    create_resp = await client.post(
        "/v1/groups",
        json={"name": "Manual Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = create_resp.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/member1",
        headers={"Authorization": f"Bearer {token}"},
    )

    await client.delete(
        f"/v1/groups/by_id/{group_id}/members/member1",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json={"gender": "female"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "auto"


# =============================================================================
# Gap-fill tests (R17, R20, R21/R22 coach, R24 member 403, R79 audit)
# =============================================================================


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.requirement("groups:R17")
@pytest.mark.asyncio
async def test_hard_delete_without_soft_delete_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """R17: hard-delete on a non-soft-deleted group is rejected."""
    token = await create_admin_user(db_session)
    g = await client.post("/v1/groups", json={"name": "Live"}, headers=_auth(token))
    gid = g.json()["id"]

    response = await client.delete(f"/v1/groups/by_id/{gid}/hard", headers=_auth(token))
    assert response.status_code == 422


@pytest.mark.requirement("groups:R20")
@pytest.mark.asyncio
async def test_coach_cannot_list_deleted_groups(
    client: AsyncClient, db_session: AsyncSession
):
    """R20: coach can't list /v1/groups/deleted (admin only)."""
    _admin = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coach1")
    response = await client.get("/v1/groups/deleted", headers=_auth(coach_token))
    assert response.status_code == 403


@pytest.mark.requirement("groups:R21")
@pytest.mark.requirement("groups:R22")
@pytest.mark.asyncio
async def test_coach_can_list_view_and_members(
    client: AsyncClient, db_session: AsyncSession
):
    """R21/R22: coach can list groups, view a group, and list its members."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coach1")
    g = await client.post(
        "/v1/groups", json={"name": "Coach View"}, headers=_auth(admin_token)
    )
    gid = g.json()["id"]

    listing = await client.get("/v1/groups", headers=_auth(coach_token))
    assert listing.status_code == 200
    assert any(item["id"] == gid for item in listing.json()["items"])

    detail = await client.get(f"/v1/groups/by_id/{gid}", headers=_auth(coach_token))
    assert detail.status_code == 200

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=_auth(coach_token)
    )
    assert members.status_code == 200


@pytest.mark.requirement("groups:R24")
@pytest.mark.asyncio
async def test_plain_member_blocked_from_groups_endpoints(
    client: AsyncClient, db_session: AsyncSession
):
    """R24: plain member receives 403 from every /v1/groups/* endpoint."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    g = await client.post("/v1/groups", json={"name": "X"}, headers=_auth(admin_token))
    gid = g.json()["id"]

    listing = await client.get("/v1/groups", headers=_auth(member_token))
    assert listing.status_code == 403

    detail = await client.get(f"/v1/groups/by_id/{gid}", headers=_auth(member_token))
    assert detail.status_code == 403

    create = await client.post(
        "/v1/groups", json={"name": "Y"}, headers=_auth(member_token)
    )
    assert create.status_code == 403


@pytest.mark.requirement("groups:R79")
@pytest.mark.asyncio
async def test_audit_log_for_group_mutations(
    client: AsyncClient, db_session: AsyncSession
):
    """R79: every group mutation writes a corresponding audit row."""
    admin_token = await create_admin_user(db_session)
    await create_user_and_approve(client, admin_token, "amy", db_session)
    await create_user_and_approve(client, admin_token, "bob", db_session)

    g = await client.post(
        "/v1/groups", json={"name": "Auditable"}, headers=_auth(admin_token)
    )
    gid = g.json()["id"]
    await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"description": "updated"},
        headers=_auth(admin_token),
    )
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=_auth(admin_token)
    )
    await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["bob"]},
        headers=_auth(admin_token),
    )
    await client.delete(
        f"/v1/groups/by_id/{gid}/members/amy", headers=_auth(admin_token)
    )
    await client.delete(f"/v1/groups/by_id/{gid}", headers=_auth(admin_token))
    await client.post(f"/v1/groups/by_id/{gid}/restore", headers=_auth(admin_token))
    await client.delete(f"/v1/groups/by_id/{gid}", headers=_auth(admin_token))
    await client.delete(f"/v1/groups/by_id/{gid}/hard", headers=_auth(admin_token))

    rows = (
        await db_session.execute(
            _sa_select(AuditLog.action).where(AuditLog.resource_type == "group")
        )
    ).all()
    actions = {r[0] for r in rows}
    expected = {
        "create_group",
        "update_group",
        "add_group_member",
        "add_group_members_bulk",
        "remove_group_member",
        "soft_delete_group",
        "restore_group",
        "wipeout_group",
    }
    assert expected.issubset(actions), actions
