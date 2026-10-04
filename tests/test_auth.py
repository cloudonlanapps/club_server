import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user


@pytest.mark.requirement("auth:R1")
@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, db_session: AsyncSession):
    """Test successful login returns JWT token."""
    _ = await create_admin_user(db_session)

    response = await client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "adminpass123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "accessToken" in data
    assert data["tokenType"] == "bearer"


@pytest.mark.requirement("auth:R2")
@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, db_session: AsyncSession):
    """Test login fails with wrong password."""
    _ = await create_admin_user(db_session)

    response = await client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert response.status_code == 401
    assert "INVALID_CREDENTIALS" in str(response.json()["detail"])


@pytest.mark.requirement("auth:R2")
@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    """Test login fails for nonexistent user."""
    response = await client.post(
        "/v1/auth/login",
        json={"username": "nobody", "password": "somepassword"},
    )
    assert response.status_code == 401
    assert "INVALID_CREDENTIALS" in str(response.json()["detail"])


@pytest.mark.requirement("auth:R11")
@pytest.mark.asyncio
async def test_me_endpoint(client: AsyncClient, db_session: AsyncSession):
    """Test /me endpoint returns current user."""
    token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert data["isSuperAdmin"] is True


@pytest.mark.requirement("auth:R8")
@pytest.mark.asyncio
async def test_me_endpoint_without_auth(client: AsyncClient):
    """Test /me endpoint fails without authentication."""
    response = await client.get("/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.requirement("users:R1")
@pytest.mark.asyncio
async def test_register_new_user(client: AsyncClient):
    """Test registering a new user creates a registered (not yet submitted) account."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "newuserpass123",
            "firstName": "New",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newuser"
    assert data["status"] == "registered"
    assert data["isSuperAdmin"] is False


@pytest.mark.requirement("users:R5")
@pytest.mark.asyncio
async def test_register_duplicate_username(client: AsyncClient):
    """Test registering duplicate username fails."""
    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "newuserpass123",
            "firstName": "New",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )

    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "newuser",
            "email": "different@example.com",
            "password": "anotherpass123",
            "firstName": "New",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 409
    assert "DUPLICATE_USERNAME" in str(response.json()["detail"])


@pytest.mark.requirement("auth:R3")
@pytest.mark.asyncio
async def test_pending_user_can_login(client: AsyncClient, db_session: AsyncSession):
    """Pending users can authenticate (#120).

    Per CHANGELOG: ``GET /v1/users/me`` / notifications / similar reads are
    available to ``pending`` users so the UI can render the "awaiting review"
    state. Login must therefore succeed for ``pending``; the gate is only
    closed for ``blocked`` and ``left``."""
    _ = await client.post(
        "/v1/auth/register",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "newuserpass123",
            "firstName": "New",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )

    # Flip to pending via the submit-for-review flow (#120).
    login_resp = await client.post(
        "/v1/auth/login",
        json={"username": "newuser", "password": "newuserpass123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["accessToken"]
    await attach_identity_document(db_session, "newuser")
    submit = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert submit.status_code == 200

    response = await client.post(
        "/v1/auth/login",
        json={"username": "newuser", "password": "newuserpass123"},
    )
    assert response.status_code == 200
    assert response.json()["accessToken"]


@pytest.mark.requirement("users:R5")
@pytest.mark.asyncio
async def test_register_duplicate_username_race_condition(client: AsyncClient):
    """A duplicate username returns 409 DUPLICATE_USERNAME, not 500.

    Registration relies on the `users` primary key (not a pre-flight check),
    so a second register with the same username hits the DB constraint and is
    mapped to 409 — closing the TOCTOU race two concurrent requests could hit.
    """
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "raceuser",
            "email": "race1@example.com",
            "password": "password123",
            "firstName": "Race",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 201

    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "raceuser",
            "email": "race2@example.com",
            "password": "password456",
            "firstName": "Race",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DUPLICATE_USERNAME"


@pytest.mark.requirement("users:R8")
@pytest.mark.asyncio
async def test_username_available_returns_true_for_free_username(client: AsyncClient):
    """Available username should return available=true with no auth required."""
    response = await client.get(
        "/v1/auth/username-available", params={"username": "freshname"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "freshname"
    assert data["available"] is True


@pytest.mark.requirement("users:R8")
@pytest.mark.asyncio
async def test_username_available_returns_false_when_taken(
    client: AsyncClient, db_session: AsyncSession
):
    """Taken username should return available=false."""
    _ = await create_admin_user(db_session)

    response = await client.get(
        "/v1/auth/username-available", params={"username": "admin"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert data["available"] is False


@pytest.mark.requirement("users:R8")
@pytest.mark.asyncio
async def test_username_available_rejects_missing_param(client: AsyncClient):
    """Missing username query param should return 422."""
    response = await client.get("/v1/auth/username-available")
    assert response.status_code == 422


@pytest.mark.requirement("users:R8")
@pytest.mark.asyncio
async def test_username_available_rejects_empty_username(client: AsyncClient):
    """Empty username should return 422 (min_length=1)."""
    response = await client.get("/v1/auth/username-available", params={"username": ""})
    assert response.status_code == 422


@pytest.mark.requirement("users:R8")
@pytest.mark.asyncio
async def test_username_available_rejects_too_long_username(client: AsyncClient):
    """Username over 50 chars should return 422 (max_length=50)."""
    response = await client.get(
        "/v1/auth/username-available", params={"username": "a" * 51}
    )
    assert response.status_code == 422


@pytest.mark.requirement("users:R8")
@pytest.mark.asyncio
async def test_username_available_requires_no_auth(client: AsyncClient):
    """Endpoint is public — no Authorization header required."""
    response = await client.get(
        "/v1/auth/username-available", params={"username": "anyone"}
    )
    assert response.status_code == 200


@pytest.mark.requirement("auth:R24")
@pytest.mark.asyncio
async def test_change_password_wrong_current_password_returns_401(
    client: AsyncClient, db_session: AsyncSession
):
    """Test change-password rejects wrong current password with 401."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": "wrongpassword", "newPassword": "newpass456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.requirement("auth:R23")
@pytest.mark.asyncio
async def test_change_password_success(client: AsyncClient, db_session: AsyncSession):
    """Test change-password succeeds and new password works for login."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": "adminpass123", "newPassword": "newpass456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Verify new password works
    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "newpass456"},
    )
    assert login_response.status_code == 200

    # Verify old password no longer works
    old_login_response = await client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "adminpass123"},
    )
    assert old_login_response.status_code == 401


# =============================================================================
# Required profile field validation
# =============================================================================


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_register_missing_name_returns_400(client: AsyncClient):
    """Registration without first_name or last_name returns 400."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "noname",
            "password": "testpass123",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "first_name or last_name" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_register_missing_gender_returns_400(client: AsyncClient):
    """Registration without gender returns 400."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "nogender",
            "password": "testpass123",
            "firstName": "Test",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "gender" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_register_missing_dob_returns_400(client: AsyncClient):
    """Registration without date_of_birth_utc returns 400."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "nodob",
            "password": "testpass123",
            "firstName": "Test",
            "gender": "male",
            "phone": "1234567890",
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "date_of_birth_utc" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_register_missing_phone_returns_400(client: AsyncClient):
    """Registration without phone returns 400."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "nophone",
            "password": "testpass123",
            "firstName": "Test",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MISSING_REQUIRED_PROFILE_FIELDS"
    assert "phone" in detail["missing_fields"]


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_register_missing_multiple_fields_returns_all(client: AsyncClient):
    """All missing fields are reported in a single response."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "empty",
            "password": "testpass123",
        },
    )
    assert response.status_code == 400
    missing = response.json()["detail"]["missing_fields"]
    assert len(missing) == 4


@pytest.mark.requirement("users:R4")
@pytest.mark.asyncio
async def test_register_first_name_only_is_valid(client: AsyncClient):
    """Having only first_name (no last_name) satisfies the name requirement."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "firstonly",
            "password": "testpass123",
            "firstName": "Alice",
            "gender": "female",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
    )
    assert response.status_code == 201


@pytest.mark.requirement("users:R4")
@pytest.mark.asyncio
async def test_register_last_name_only_is_valid(client: AsyncClient):
    """Having only last_name (no first_name) satisfies the name requirement."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "lastonly",
            "password": "testpass123",
            "lastName": "Smith",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
    )
    assert response.status_code == 201


@pytest.mark.requirement("users:R3")
@pytest.mark.asyncio
async def test_register_whitespace_name_returns_400(client: AsyncClient):
    """Whitespace-only names are treated as empty."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "wsname",
            "password": "testpass123",
            "firstName": "   ",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 400
    assert "first_name or last_name" in response.json()["detail"]["missing_fields"]


# =============================================================================
# Duplicate email, logout, refresh token, password reset
# =============================================================================


@pytest.mark.requirement("users:R6")
@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    """Registering with an already-used email returns 409 DUPLICATE_EMAIL."""
    await client.post(
        "/v1/auth/register",
        json={
            "username": "user1",
            "email": "shared@example.com",
            "password": "password123",
            "firstName": "First",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )

    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "user2",
            "email": "shared@example.com",
            "password": "password456",
            "firstName": "Second",
            "gender": "female",
            "dateOfBirthUtc": 946684800000,
            "phone": "9876543210",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DUPLICATE_EMAIL"


@pytest.mark.requirement("auth:R21")
@pytest.mark.asyncio
async def test_logout_invalidates_token(client: AsyncClient, db_session: AsyncSession):
    """After logout, the token no longer grants access (#510)."""
    token = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401
    assert me.json()["detail"]["code"] == "INVALID_TOKEN"


@pytest.mark.requirement("auth:R15")
@pytest.mark.asyncio
async def test_refresh_token_success(client: AsyncClient, db_session: AsyncSession):
    """A valid refresh token returns a new access token."""
    _ = await create_admin_user(db_session)

    login_response = await client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "adminpass123"},
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refreshToken"]

    response = await client.post(
        "/v1/auth/refresh",
        json={"refreshToken": refresh_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "accessToken" in data
    assert "refreshToken" in data
    assert "expiresAtUtc" in data


@pytest.mark.requirement("auth:R16")
@pytest.mark.asyncio
async def test_refresh_token_invalid(client: AsyncClient):
    """An invalid refresh token returns 401."""
    response = await client.post(
        "/v1/auth/refresh",
        json={"refreshToken": "invalid.token.value"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"


@pytest.mark.requirement("auth:R28")
@pytest.mark.asyncio
async def test_password_reset_existing_email(client: AsyncClient):
    """Password reset for a known email returns 204."""
    await client.post(
        "/v1/auth/register",
        json={
            "username": "resetuser",
            "email": "resetuser@example.com",
            "password": "password123",
            "firstName": "Reset",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )

    response = await client.post(
        "/v1/auth/reset-password",
        json={"email": "resetuser@example.com"},
    )
    assert response.status_code == 204


@pytest.mark.requirement("auth:R28")
@pytest.mark.asyncio
async def test_password_reset_unknown_email_no_error(client: AsyncClient):
    """Password reset for an unknown email still returns 204 (no info leak)."""
    response = await client.post(
        "/v1/auth/reset-password",
        json={"email": "nonexistent@example.com"},
    )
    assert response.status_code == 204


@pytest.mark.requirement("auth:R25")
@pytest.mark.asyncio
async def test_change_password_without_auth(client: AsyncClient):
    """Change-password without authentication returns 401."""
    response = await client.post(
        "/v1/auth/change-password",
        json={"currentPassword": "anything", "newPassword": "newpass456"},
    )
    assert response.status_code == 401
