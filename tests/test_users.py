from typing import cast
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_registered_user,
    create_regular_admin_user,
)


async def create_pending_user(
    client: AsyncClient, username: str, db_session: AsyncSession
) -> dict[str, str]:
    """Helper to create a pending user (registers and submits for review)."""
    response = await client.post(
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
    body = cast(dict[str, str], response.json())
    login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    assert login.status_code == 200, login.text
    user_token = login.json()["accessToken"]
    await attach_identity_document(db_session, username)
    submit = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert submit.status_code == 200, submit.text
    return body


@pytest.mark.requirement("users:R41")
@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, db_session: AsyncSession):
    """Test listing users as admin."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    # Super admin is excluded from user lists
    assert len(data["items"]) == 0


@pytest.mark.requirement("users:R44")
@pytest.mark.asyncio
async def test_list_users_without_auth(client: AsyncClient):
    """Test listing users fails without authentication."""
    response = await client.get("/v1/users")
    assert response.status_code == 401


@pytest.mark.requirement("users:R41")
@pytest.mark.asyncio
async def test_list_users_as_coach(client: AsyncClient, db_session: AsyncSession):
    """Coaches can list users (needed for enrollment user-picker dialog, #164)."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)

    # Seed one regular user so the list has content visible to the coach.
    await create_pending_user(client, "alice", db_session)
    await client.patch(
        "/v1/users/by_id/alice/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )

    response = await client.get(
        "/v1/users",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    usernames = {item["username"] for item in data["items"]}
    assert "alice" in usernames
    assert "coach" in usernames


@pytest.mark.requirement("users:R48")
@pytest.mark.asyncio
async def test_get_user_private_as_coach(client: AsyncClient, db_session: AsyncSession):
    """Coaches can read another user's private profile (#164)."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)

    await create_pending_user(client, "alice", db_session)
    await client.patch(
        "/v1/users/by_id/alice/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )

    response = await client.get(
        "/v1/users/by_id/alice/private",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "alice"


@pytest.mark.requirement("users:R47")
@pytest.mark.asyncio
async def test_get_user_detail(client: AsyncClient, db_session: AsyncSession):
    """Test getting user detail."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/users/by_id/admin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert data["isSuperAdmin"] is True
    assert data["deletedAtUtc"] is None


@pytest.mark.requirement("users:R47")
@pytest.mark.asyncio
async def test_get_nonexistent_user(client: AsyncClient, db_session: AsyncSession):
    """Test getting nonexistent user returns 404."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/users/by_id/nobody",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.requirement("users:R12")
@pytest.mark.asyncio
async def test_approve_pending_user(client: AsyncClient, db_session: AsyncSession):
    """Test approving a pending user."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    # Approve user
    response = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"

    # Verify user can now login
    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "newuser", "password": "testpass123"},
    )
    assert login_response.status_code == 200


@pytest.mark.requirement("users:R14")
@pytest.mark.asyncio
async def test_approve_non_pending_user(client: AsyncClient, db_session: AsyncSession):
    """Test approving already active user fails."""
    token = await create_admin_user(db_session)

    # Try to approve already active admin
    response = await client.post(
        "/v1/users/by_id/admin/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "INVALID_STATE" in str(response.json()["detail"])


@pytest.mark.requirement("users:R24")
@pytest.mark.asyncio
async def test_block_user(client: AsyncClient, db_session: AsyncSession):
    """Test blocking a user."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    # Approve user first
    _ = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Block user
    response = await client.post(
        "/v1/users/by_id/newuser/block",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked"


@pytest.mark.requirement("users:R59")
@pytest.mark.asyncio
async def test_block_super_admin_fails(client: AsyncClient, db_session: AsyncSession):
    """Test blocking super admin fails."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users/by_id/admin/block",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_PROTECTION"


@pytest.mark.requirement("users:R25")
@pytest.mark.asyncio
async def test_unblock_user(client: AsyncClient, db_session: AsyncSession):
    """Test unblocking a blocked user."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    # Approve and block user
    _ = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/users/by_id/newuser/block",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Unblock user
    response = await client.post(
        "/v1/users/by_id/newuser/unblock",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"


@pytest.mark.requirement("users:R30")
@pytest.mark.asyncio
async def test_assign_role(client: AsyncClient, db_session: AsyncSession):
    """Test assigning a role to user."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    # Approve user first
    _ = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Assign role
    response = await client.post(
        "/v1/users/by_id/newuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "coach" in data["roles"]


@pytest.mark.requirement("users:R31")
@pytest.mark.asyncio
async def test_assign_duplicate_role(client: AsyncClient, db_session: AsyncSession):
    """Test assigning duplicate role fails."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    _ = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/users/by_id/newuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Try to assign same role again
    response = await client.post(
        "/v1/users/by_id/newuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert "ROLE_ALREADY_ASSIGNED" in str(response.json()["detail"])


@pytest.mark.requirement("users:R30")
@pytest.mark.asyncio
async def test_remove_role(client: AsyncClient, db_session: AsyncSession):
    """Test removing a role from user."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    _ = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    _ = await client.post(
        "/v1/users/by_id/newuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Remove role
    response = await client.delete(
        "/v1/users/by_id/newuser/roles/coach",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "coach" not in data["roles"]


@pytest.mark.requirement("users:R31")
@pytest.mark.asyncio
async def test_remove_nonexistent_role(client: AsyncClient, db_session: AsyncSession):
    """Test removing nonexistent role fails."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "newuser", db_session)

    _ = await client.post(
        "/v1/users/by_id/newuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Try to remove role that wasn't assigned
    response = await client.delete(
        "/v1/users/by_id/newuser/roles/coach",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert "ROLE_NOT_FOUND" in str(response.json()["detail"])


# =============================================================================
# List deleted users
# =============================================================================


@pytest.mark.requirement("users:R51")
@pytest.mark.asyncio
async def test_list_deleted_users_empty(client: AsyncClient, db_session: AsyncSession):
    """List deleted users returns empty when no users are deleted."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/users/deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.requirement("users:R50")
@pytest.mark.requirement("users:R51")
@pytest.mark.requirement("users:R41")
@pytest.mark.asyncio
async def test_list_deleted_users(client: AsyncClient, db_session: AsyncSession):
    """Soft-deleted users appear in /users/deleted but not in /users."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "tobedeleted", db_session)

    # Approve then delete
    await client.post(
        "/v1/users/by_id/tobedeleted/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.delete(
        "/v1/users/by_id/tobedeleted",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Deleted user appears in /users/deleted with deletedAtUtc
    response = await client.get(
        "/v1/users/deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["username"] == "tobedeleted"
    assert data["items"][0]["deletedAtUtc"] is not None

    # Deleted user does NOT appear in /users
    response = await client.get(
        "/v1/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    usernames = [u["username"] for u in response.json()["items"]]
    assert "tobedeleted" not in usernames

    # Get by username includes deletedAtUtc when soft deleted
    get_response = await client.get(
        "/v1/users/by_id/tobedeleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is not None

    # Get private profile also includes deletedAtUtc when soft deleted
    private_response = await client.get(
        "/v1/users/by_id/tobedeleted/private",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert private_response.status_code == 200
    assert private_response.json()["deletedAtUtc"] is not None


@pytest.mark.requirement("users:R51")
@pytest.mark.asyncio
async def test_list_deleted_users_search(client: AsyncClient, db_session: AsyncSession):
    """List deleted users supports searchTerm filter."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "alice_del", db_session)
    _ = await create_pending_user(client, "bob_del", db_session)

    for name in ["alice_del", "bob_del"]:
        await client.post(
            f"/v1/users/by_id/{name}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.delete(
            f"/v1/users/by_id/{name}",
            headers={"Authorization": f"Bearer {token}"},
        )

    response = await client.get(
        "/v1/users/deleted?searchTerm=alice",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["username"] == "alice_del"


@pytest.mark.asyncio
async def test_list_deleted_users_requires_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """List deleted users without auth returns 401."""
    response = await client.get("/v1/users/deleted")
    assert response.status_code in (401, 403)


@pytest.mark.requirement("users:R51")
@pytest.mark.asyncio
async def test_list_deleted_users_pagination(
    client: AsyncClient, db_session: AsyncSession
):
    """List deleted users supports offset and limit."""
    token = await create_admin_user(db_session)

    for i in range(3):
        _ = await create_pending_user(client, f"del_user_{i}", db_session)
        await client.post(
            f"/v1/users/by_id/del_user_{i}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.delete(
            f"/v1/users/by_id/del_user_{i}",
            headers={"Authorization": f"Bearer {token}"},
        )

    response = await client.get(
        "/v1/users/deleted?limit=2&offset=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2


# =============================================================================
# Gender and address fields
# =============================================================================


@pytest.mark.requirement("users:R9c")
@pytest.mark.asyncio
async def test_register_with_gender_and_address(
    client: AsyncClient, db_session: AsyncSession
):
    """Registration with gender and address fields persists in private profile."""
    token = await create_admin_user(db_session)

    # Register with gender and address
    reg_response = await client.post(
        "/v1/auth/register",
        json={
            "username": "genuser",
            "password": "testpass123",
            "email": "genuser@example.com",
            "firstName": "Gen",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
            "address": {
                "addrLine1": "123 Main St",
                "addrLine2": "Apt 4",
                "city": "Chennai",
                "state": "TN",
                "pincode": "600001",
            },
        },
    )
    assert reg_response.status_code == 201
    reg_data = reg_response.json()
    assert reg_data["gender"] == "male"
    assert reg_data["address"]["addrLine1"] == "123 Main St"
    assert reg_data["address"]["addrLine2"] == "Apt 4"
    assert reg_data["address"]["city"] == "Chennai"
    assert reg_data["address"]["state"] == "TN"
    assert reg_data["address"]["pincode"] == "600001"

    # Verify via admin private profile fetch
    response = await client.get(
        "/v1/users/by_id/genuser/private",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["gender"] == "male"
    assert data["address"]["addrLine1"] == "123 Main St"
    assert data["address"]["pincode"] == "600001"


# =============================================================================
# Required profile field validation (admin create)
# =============================================================================


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_admin_create_missing_name_returns_400(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin create without first_name or last_name returns 400."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users",
        json={
            "username": "noname",
            "passwordHash": "somehash",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "first_name or last_name" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_admin_create_missing_gender_returns_400(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin create without gender returns 400."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users",
        json={
            "username": "nogender",
            "passwordHash": "somehash",
            "firstName": "Test",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "gender" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_admin_create_missing_dob_returns_400(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin create without date_of_birth_utc returns 400."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users",
        json={
            "username": "nodob",
            "passwordHash": "somehash",
            "firstName": "Test",
            "gender": "male",
            "phone": "1234567890",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "date_of_birth_utc" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_admin_create_missing_phone_returns_400(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin create without phone returns 400."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users",
        json={
            "username": "nophone",
            "passwordHash": "somehash",
            "firstName": "Test",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "phone" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_admin_create_missing_multiple_fields_returns_all(
    client: AsyncClient, db_session: AsyncSession
):
    """All missing fields are reported in a single response."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users",
        json={
            "username": "empty",
            "passwordHash": "somehash",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    missing = response.json()["detail"]["missing_fields"]
    assert len(missing) == 4


@pytest.mark.requirement("users:R9c")
@pytest.mark.asyncio
async def test_admin_create_with_gender_and_address(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin-created user with gender and address fields."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/users",
        json={
            "username": "adminmade",
            "passwordHash": "somehash",
            "firstName": "Admin",
            "gender": "female",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
            "address": {
                "addrLine1": "456 Oak Ave",
                "city": "Mumbai",
                "state": "MH",
                "pincode": "400001",
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["gender"] == "female"
    assert data["address"]["addrLine1"] == "456 Oak Ave"
    assert data["address"]["city"] == "Mumbai"
    assert data["address"]["addrLine2"] is None


@pytest.mark.requirement("users:R9c")
@pytest.mark.asyncio
async def test_update_gender_and_address(client: AsyncClient, db_session: AsyncSession):
    """Updating gender and address via PATCH."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "upduser", db_session)

    # Update with gender and address
    response = await client.patch(
        "/v1/users/by_id/upduser",
        json={
            "gender": "other",
            "address": {
                "addrLine1": "789 Pine Rd",
                "city": "Delhi",
                "state": "DL",
                "pincode": "110001",
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["gender"] == "other"
    assert data["address"]["addrLine1"] == "789 Pine Rd"
    assert data["address"]["city"] == "Delhi"

    # Verify via private profile
    verify = await client.get(
        "/v1/users/by_id/upduser/private",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["gender"] == "other"
    assert verify.json()["address"]["pincode"] == "110001"


@pytest.mark.requirement("users:R9c")
@pytest.mark.asyncio
async def test_address_default_null(client: AsyncClient, db_session: AsyncSession):
    """User created without address has null for address."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "nulluser", db_session)

    response = await client.get(
        "/v1/users/by_id/nulluser/private",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["gender"] == "male"
    assert data["address"] is None


@pytest.mark.requirement("users:R9b")
@pytest.mark.asyncio
async def test_invalid_gender_rejected(client: AsyncClient):
    """Invalid gender value returns 422."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "badgender",
            "password": "testpass123",
            "firstName": "Test",
            "gender": "invalid",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 422


@pytest.mark.requirement("users:R9c")
@pytest.mark.asyncio
async def test_partial_address(client: AsyncClient, db_session: AsyncSession):
    """Address with only some fields set, others default to null."""
    _ = await create_admin_user(db_session)

    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "partaddr",
            "password": "testpass123",
            "firstName": "Part",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
            "address": {
                "addrLine1": "Only street",
                "pincode": "123456",
            },
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["address"]["addrLine1"] == "Only street"
    assert data["address"]["pincode"] == "123456"
    assert data["address"]["addrLine2"] is None
    assert data["address"]["city"] is None
    assert data["address"]["state"] is None


@pytest.mark.requirement("users:R50")
@pytest.mark.requirement("users:R47")
@pytest.mark.asyncio
async def test_soft_delete_user(client: AsyncClient, db_session: AsyncSession):
    """Test soft deleting a user sets deletedAtUtc."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "softdel", db_session)
    await client.post(
        "/v1/users/by_id/softdel/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.delete(
        "/v1/users/by_id/softdel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["deletedAtUtc"] is not None

    # Verify user is still accessible via GET but has deletedAtUtc set
    get_response = await client.get(
        "/v1/users/by_id/softdel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is not None


@pytest.mark.requirement("users:R52")
@pytest.mark.asyncio
async def test_restore_user(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a soft-deleted user clears deletedAtUtc."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "torestore", db_session)
    await client.post(
        "/v1/users/by_id/torestore/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete
    await client.delete(
        "/v1/users/by_id/torestore",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Restore
    restore_response = await client.post(
        "/v1/users/by_id/torestore/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["username"] == "torestore"
    assert restore_response.json()["deletedAtUtc"] is None

    # Verify user is visible again with no deletedAtUtc
    get_response = await client.get(
        "/v1/users/by_id/torestore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["deletedAtUtc"] is None


@pytest.mark.requirement("users:R53")
@pytest.mark.asyncio
async def test_restore_user_not_deleted(client: AsyncClient, db_session: AsyncSession):
    """Test restoring a user that is not deleted fails."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "activeuser", db_session)
    await client.post(
        "/v1/users/by_id/activeuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    restore_response = await client.post(
        "/v1/users/by_id/activeuser/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 422


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
async def test_hard_delete_user(client: AsyncClient, db_session: AsyncSession):
    """Test permanently deleting a user (super admin)."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "toremove", db_session)
    await client.post(
        "/v1/users/by_id/toremove/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete first (required before hard delete)
    await client.delete(
        "/v1/users/by_id/toremove",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.delete(
        "/v1/users/by_id/toremove/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Verify user is permanently gone
    get_response = await client.get(
        "/v1/users/by_id/toremove",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 404


@pytest.mark.requirement("users:R55")
@pytest.mark.asyncio
async def test_hard_delete_user_requires_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard delete requires super admin."""
    super_admin_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_pending_user(client, "protected", db_session)
    await client.post(
        "/v1/users/by_id/protected/approve",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )

    response = await client.delete(
        "/v1/users/by_id/protected/hard",
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 403


@pytest.mark.requirement("users:R59")
@pytest.mark.asyncio
async def test_hard_delete_super_admin_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that super admin cannot be hard deleted."""
    token = await create_admin_user(db_session)

    response = await client.delete(
        "/v1/users/by_id/admin/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
async def test_hard_delete_user_cascades_enrollments(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard deleting a user cascades to their enrollments."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "enrolled_user", db_session)
    await client.post(
        "/v1/users/by_id/enrolled_user/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Create venue and event
    venue_response = await client.post(
        "/v1/venues",
        json={"name": "Cascade Test Venue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    venue_id = venue_response.json()["id"]

    from datetime import datetime, timedelta, timezone

    def future_time(hours: int) -> int:
        return int(
            (datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000
        )

    event_response = await client.post(
        "/v1/events",
        json={
            "title": "Cascade Test Event",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": future_time(24),
            "endTimeUtc": future_time(25),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    event_id = event_response.json()["id"]

    # Enroll the user
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments",
        json={"membername": "enrolled_user"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete first (required before hard delete)
    await client.delete(
        "/v1/users/by_id/enrolled_user",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Hard delete the user — should not raise FK violation
    response = await client.delete(
        "/v1/users/by_id/enrolled_user/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Verify user is gone
    get_response = await client.get(
        "/v1/users/by_id/enrolled_user",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 404


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
async def test_hard_delete_user_cascades_group_membership(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard deleting a user cascades to their group memberships."""
    token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "grouped_user", db_session)
    await client.post(
        "/v1/users/by_id/grouped_user/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Create group and add user
    group_response = await client.post(
        "/v1/groups",
        json={"name": "Cascade Test Group"},
        headers={"Authorization": f"Bearer {token}"},
    )
    group_id = group_response.json()["id"]

    await client.post(
        f"/v1/groups/by_id/{group_id}/members",
        json={"membernames": ["grouped_user"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Soft delete first (required before hard delete)
    await client.delete(
        "/v1/users/by_id/grouped_user",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Hard delete the user — should not raise FK violation
    response = await client.delete(
        "/v1/users/by_id/grouped_user/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Group still exists, but user is no longer a member
    group_response = await client.get(
        f"/v1/groups/by_id/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert group_response.status_code == 200
    members = group_response.json()["members"]
    member_names = [m["membername"] for m in members]
    assert "grouped_user" not in member_names


@pytest.mark.requirement("notifications:R39")
@pytest.mark.asyncio
async def test_hard_delete_user_clears_actionable_notifications_keyed_to_user(
    client: AsyncClient, db_session: AsyncSession
):
    """Hard-deleting a user must remove notifications addressed to other
    recipients whose pending_action_key references the deleted user, since
    that action can never resolve. Informational notifications that mention
    the deleted user only inside payload.data (no pending_action_key) must
    survive — they remain a legitimate audit-style record for the recipient.
    """
    from sqlalchemy import select as _select

    from club_server.db.models.notification import Notification

    token = await create_admin_user(db_session)

    # Register + submit "alice" — this enqueues a user_approval notification
    # to admins with pending_action_key="alice".
    _ = await create_pending_user(client, "alice", db_session)

    # Sanity: the actionable user_approval notification exists for admin.
    pre = await db_session.execute(
        _select(Notification).where(
            Notification.pending_action_key == "alice",
            Notification.pending_action_type == "user_approval",
        )
    )
    pre_rows = pre.scalars().all()
    assert len(pre_rows) >= 1

    # Seed an informational notification mentioning alice as actor but with
    # NO pending_action_key — this represents historical "alice did X to you"
    # alerts that must survive alice's deletion.
    info = Notification(
        username="admin",
        type="profile.changed_by_admin",
        channel="alert",
        payload={
            "v": 1,
            "type": "profile.changed_by_admin",
            "data": {"actorUsername": "alice", "username": "admin"},
        },
        pending_action_type=None,
        pending_action_id=None,
        pending_action_key=None,
        is_read=0,
        created_at=1700000000000,
    )
    db_session.add(info)
    await db_session.flush()
    info_id = info.id

    # Soft delete, then hard delete alice.
    soft = await client.delete(
        "/v1/users/by_id/alice",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert soft.status_code == 200, soft.text
    hard = await client.delete(
        "/v1/users/by_id/alice/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hard.status_code == 204, hard.text

    # Actionable notification keyed to alice must be gone.
    post = await db_session.execute(
        _select(Notification).where(Notification.pending_action_key == "alice")
    )
    assert post.scalars().all() == []

    # Informational notification mentioning alice as actor must survive.
    survivor = await db_session.execute(
        _select(Notification).where(Notification.id == info_id)
    )
    assert survivor.scalar_one_or_none() is not None


# =============================================================================
# Transfer superadmin, mark left, reactivate, search, filter, count, privacy
# =============================================================================


@pytest.mark.requirement("users:R56")
@pytest.mark.asyncio
async def test_transfer_superadmin(client: AsyncClient, db_session: AsyncSession):
    """Test transferring super admin role to another user."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "newadmin", db_session)
    await client.post(
        "/v1/users/by_id/newadmin/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        "/v1/users/by_id/newadmin/transfer-superadmin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "newadmin"
    assert data["isSuperAdmin"] is True


@pytest.mark.requirement("users:R57")
@pytest.mark.asyncio
async def test_transfer_superadmin_non_superadmin_fails(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that non-super-admin cannot transfer super admin role."""
    _ = await create_admin_user(db_session)
    regular_token = await create_regular_admin_user(db_session)

    await create_pending_user(client, "targetuser", db_session)
    await client.post(
        "/v1/users/by_id/targetuser/approve",
        headers={"Authorization": f"Bearer {regular_token}"},
    )

    response = await client.post(
        "/v1/users/by_id/targetuser/transfer-superadmin",
        headers={"Authorization": f"Bearer {regular_token}"},
    )
    assert response.status_code == 403


@pytest.mark.requirement("users:R27")
@pytest.mark.asyncio
async def test_mark_user_left(client: AsyncClient, db_session: AsyncSession):
    """Test marking a user as left."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "leavinguser", db_session)
    await client.post(
        "/v1/users/by_id/leavinguser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        "/v1/users/by_id/leavinguser/mark-left",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "left"


@pytest.mark.requirement("users:R27")
@pytest.mark.asyncio
async def test_reactivate_left_user(client: AsyncClient, db_session: AsyncSession):
    """Test reactivating a user who was marked as left."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "leavinguser", db_session)
    await client.post(
        "/v1/users/by_id/leavinguser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/v1/users/by_id/leavinguser/mark-left",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.post(
        "/v1/users/by_id/leavinguser/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"


@pytest.mark.requirement("users:R42")
@pytest.mark.asyncio
async def test_list_users_search_by_term(client: AsyncClient, db_session: AsyncSession):
    """Test searching users by search term."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "alice_smith", db_session)
    await client.post(
        "/v1/users/by_id/alice_smith/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    await create_pending_user(client, "bob_jones", db_session)
    await client.post(
        "/v1/users/by_id/bob_jones/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/users",
        params={"searchTerm": "alice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    usernames = [u["username"] for u in items]
    assert "alice_smith" in usernames
    assert "bob_jones" not in usernames


@pytest.mark.requirement("users:R42")
@pytest.mark.asyncio
async def test_list_users_filter_by_status(
    client: AsyncClient, db_session: AsyncSession
):
    """Test filtering users by status."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "pendinguser", db_session)
    await create_pending_user(client, "activeuser", db_session)
    await client.post(
        "/v1/users/by_id/activeuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/users",
        params={"status": "pending"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    usernames = [u["username"] for u in items]
    assert "pendinguser" in usernames
    assert "activeuser" not in usernames


@pytest.mark.requirement("users:R41")
@pytest.mark.asyncio
async def test_list_users_excludes_registered_by_default(
    client: AsyncClient, db_session: AsyncSession
):
    """Default list_users excludes users with status=registered (issue #133)."""
    token = await create_admin_user(db_session)
    await create_registered_user(db_session, username="newbie")
    await create_pending_user(client, "pendingfellow", db_session)

    response = await client.get(
        "/v1/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    usernames = [u["username"] for u in body["items"]]
    assert "newbie" not in usernames
    assert "pendingfellow" in usernames


@pytest.mark.requirement("users:R41")
@pytest.mark.asyncio
async def test_list_users_explicit_status_registered_returns_them(
    client: AsyncClient, db_session: AsyncSession
):
    """Explicit ?status=registered still surfaces registered users (issue #133)."""
    token = await create_admin_user(db_session)
    await create_registered_user(db_session, username="newbie")
    await create_pending_user(client, "pendingfellow", db_session)

    response = await client.get(
        "/v1/users",
        params={"status": "registered"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    usernames = [u["username"] for u in response.json()["items"]]
    assert usernames == ["newbie"]


@pytest.mark.requirement("users:R42")
@pytest.mark.asyncio
async def test_list_users_filter_by_role(client: AsyncClient, db_session: AsyncSession):
    """Test filtering users by role."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "coachuser", db_session)
    await client.post(
        "/v1/users/by_id/coachuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/v1/users/by_id/coachuser/roles",
        json={"role": "coach"},
        headers={"Authorization": f"Bearer {token}"},
    )

    await create_pending_user(client, "regularuser", db_session)
    await client.post(
        "/v1/users/by_id/regularuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = await client.get(
        "/v1/users",
        params={"role": "coach"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    usernames = [u["username"] for u in items]
    assert "coachuser" in usernames
    assert "regularuser" not in usernames


@pytest.mark.requirement("users:R55")
@pytest.mark.asyncio
async def test_hard_delete_requires_soft_delete_first(
    client: AsyncClient, db_session: AsyncSession
):
    """Test that hard-deleting a user who hasn't been soft-deleted fails."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "targetuser", db_session)
    await client.post(
        "/v1/users/by_id/targetuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Try hard delete without soft delete first
    response = await client.delete(
        "/v1/users/by_id/targetuser/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in (400, 422)


@pytest.mark.requirement("users:R38")
@pytest.mark.asyncio
async def test_privacy_preference_toggle(client: AsyncClient, db_session: AsyncSession):
    """Test toggling use_name_publicly privacy preference."""
    token = await create_admin_user(db_session)

    await create_pending_user(client, "privuser", db_session)
    await client.post(
        "/v1/users/by_id/privuser/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Default is false
    get_response = await client.get(
        "/v1/users/by_id/privuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["useNamePublicly"] is False

    # Toggle to true
    update_response = await client.patch(
        "/v1/users/by_id/privuser",
        json={"useNamePublicly": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_response.status_code == 200

    # Verify it persisted
    get_response2 = await client.get(
        "/v1/users/by_id/privuser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response2.json()["useNamePublicly"] is True


@pytest.mark.requirement("users:R40")
@pytest.mark.asyncio
async def test_admin_created_user_can_login(
    client: AsyncClient, db_session: AsyncSession
):
    """Admin-created user can log in with the password set at creation time (#145)."""
    token = await create_admin_user(db_session)

    # Admin creates a user via POST /v1/users
    create_response = await client.post(
        "/v1/users",
        json={
            "username": "newplayer",
            "passwordHash": "SecurePass123",
            "firstName": "New",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 201

    # The admin-created user should be able to log in with the provided password
    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "newplayer", "password": "SecurePass123"},
    )
    assert login_response.status_code == 200
    assert "accessToken" in login_response.json()


@pytest.mark.requirement("users:R35")
@pytest.mark.asyncio
async def test_self_cannot_update_gender(client: AsyncClient, db_session: AsyncSession):
    """A user editing their own profile cannot change gender (#86)."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="self1", gender="male")
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/self1",
        json={"gender": "female"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "PROTECTED_FIELDS"
    assert detail["fields"] == ["gender"]

    verify = await client.get(
        "/v1/users/by_id/self1/private",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["gender"] == "male"


@pytest.mark.requirement("users:R35")
@pytest.mark.asyncio
async def test_self_cannot_update_date_of_birth(
    client: AsyncClient, db_session: AsyncSession
):
    """A user editing their own profile cannot change date of birth (#86)."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="self2", date_of_birth=946684800000, gender="male"
    )
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/self2",
        json={"dateOfBirthUtc": 1000000000000},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "PROTECTED_FIELDS"
    assert detail["fields"] == ["dateOfBirthUtc"]

    verify = await client.get(
        "/v1/users/by_id/self2/private",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["dateOfBirthUtc"] == 946684800000


@pytest.mark.requirement("users:R35")
@pytest.mark.asyncio
async def test_regular_admin_cannot_update_protected_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """A non-super admin cannot change gender or date of birth on any user (#86)."""
    super_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_pending_user(client, "target1", db_session)

    response = await client.patch(
        "/v1/users/by_id/target1",
        json={"gender": "female", "dateOfBirthUtc": 1000000000000},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "PROTECTED_FIELDS"
    assert set(detail["fields"]) == {"gender", "dateOfBirthUtc"}

    verify = await client.get(
        "/v1/users/by_id/target1/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["gender"] == "male"
    assert verify.json()["dateOfBirthUtc"] == 946684800000


@pytest.mark.requirement("users:R34")
@pytest.mark.asyncio
async def test_regular_admin_can_update_other_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """A non-super admin can still update non-protected fields (#86)."""
    super_token = await create_admin_user(db_session)
    regular_admin_token = await create_regular_admin_user(db_session)
    _ = await create_pending_user(client, "target2", db_session)

    response = await client.patch(
        "/v1/users/by_id/target2",
        json={"firstName": "Renamed", "bio": "Hello"},
        headers={"Authorization": f"Bearer {regular_admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["firstName"] == "Renamed"

    verify = await client.get(
        "/v1/users/by_id/target2/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["firstName"] == "Renamed"
    assert verify.json()["bio"] == "Hello"


@pytest.mark.requirement("users:R36")
@pytest.mark.asyncio
async def test_super_admin_can_update_protected_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """Super admin can change gender and date of birth (#86)."""
    super_token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "target3", db_session)

    response = await client.patch(
        "/v1/users/by_id/target3",
        json={"gender": "female", "dateOfBirthUtc": 999993600000},
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["gender"] == "female"
    assert data["dateOfBirthUtc"] == 999993600000

    verify = await client.get(
        "/v1/users/by_id/target3/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["gender"] == "female"
    assert verify.json()["dateOfBirthUtc"] == 999993600000


@pytest.mark.requirement("users:R36")
@pytest.mark.asyncio
async def test_super_admin_can_clear_date_of_birth(
    client: AsyncClient, db_session: AsyncSession
):
    """Super admin can clear dateOfBirthUtc via explicit null (#86)."""
    super_token = await create_admin_user(db_session)
    _ = await create_pending_user(client, "target4", db_session)

    response = await client.patch(
        "/v1/users/by_id/target4",
        json={"dateOfBirthUtc": None},
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert response.status_code == 200
    assert response.json()["dateOfBirthUtc"] is None

    verify = await client.get(
        "/v1/users/by_id/target4/private",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert verify.status_code == 200
    assert verify.json()["dateOfBirthUtc"] is None


# ---------------------------------------------------------------------------
# Admin create: duplicate username / email rejection (#67)
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R5")
@pytest.mark.asyncio
async def test_admin_create_duplicate_username_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /users with an existing username returns 409 DUPLICATE_USERNAME (#67)."""
    token = await create_admin_user(db_session)

    payload = {
        "username": "dupuser",
        "passwordHash": "SecurePass123",
        "firstName": "First",
        "gender": "male",
        "dateOfBirthUtc": 946684800000,
        "phone": "9876543210",
    }

    first = await client.post(
        "/v1/users",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 201

    second_payload = {**payload, "phone": "9876500000"}
    second = await client.post(
        "/v1/users",
        json=second_payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "DUPLICATE_USERNAME"

    # Confirm only the original user exists and is unchanged.
    listing = await client.get(
        f"/v1/users/by_id/{payload['username']}/private",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listing.status_code == 200
    assert listing.json()["phone"] == payload["phone"]


@pytest.mark.requirement("users:R6")
@pytest.mark.asyncio
async def test_admin_create_duplicate_email_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /users with an existing email returns 409 DUPLICATE_EMAIL (#67)."""
    token = await create_admin_user(db_session)

    first = await client.post(
        "/v1/users",
        json={
            "username": "emailowner",
            "email": "shared@example.com",
            "passwordHash": "SecurePass123",
            "firstName": "First",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 201

    second = await client.post(
        "/v1/users",
        json={
            "username": "otheruser",
            "email": "shared@example.com",
            "passwordHash": "SecurePass123",
            "firstName": "Second",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876500000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "DUPLICATE_EMAIL"

    # Confirm the second user was not created.
    missing = await client.get(
        "/v1/users/by_id/otheruser/private",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert missing.status_code == 404


@pytest.mark.requirement("users:R6")
@pytest.mark.asyncio
async def test_admin_create_duplicate_email_case_insensitive_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    """Emails differing only by case collide via the DB index (#204)."""
    token = await create_admin_user(db_session)

    first = await client.post(
        "/v1/users",
        json={
            "username": "emailowner",
            "email": "Shared@Example.com",
            "passwordHash": "SecurePass123",
            "firstName": "First",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 201

    second = await client.post(
        "/v1/users",
        json={
            "username": "otheruser",
            "email": "shared@example.com",
            "passwordHash": "SecurePass123",
            "firstName": "Second",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876500000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "DUPLICATE_EMAIL"


@pytest.mark.requirement("users:R7")
@pytest.mark.asyncio
async def test_admin_create_reuses_email_of_soft_deleted_user(
    client: AsyncClient, db_session: AsyncSession
):
    """A soft-deleted user frees its email; the partial index excludes it (#204)."""
    token = await create_admin_user(db_session)
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post(
        "/v1/users",
        json={
            "username": "leaver",
            "email": "reuse@example.com",
            "passwordHash": "SecurePass123",
            "firstName": "First",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
        headers=headers,
    )
    assert first.status_code == 201

    deleted = await client.delete("/v1/users/by_id/leaver", headers=headers)
    assert deleted.status_code == 200

    # Same email is now reusable by a brand-new active user.
    second = await client.post(
        "/v1/users",
        json={
            "username": "newcomer",
            "email": "reuse@example.com",
            "passwordHash": "SecurePass123",
            "firstName": "Second",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876500000",
        },
        headers=headers,
    )
    assert second.status_code == 201
