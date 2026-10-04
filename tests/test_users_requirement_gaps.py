"""Tests for users rules that had no proving test (#495): account states.

Each test names the rule it proves in ``docs/users_requirements.md``.
Access, profile, reads and audit are in
``test_users_access_requirement_gaps.py``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_member_user,
    create_registered_user,
    create_regular_admin_user,
    create_user_with_status,
)

MIDNIGHT_2000 = 946684800000  # 2000-01-01 UTC midnight


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _profile(client: AsyncClient, admin: str, username: str) -> dict:
    response = await client.get(f"/v1/users/by_id/{username}", headers=auth(admin))
    assert response.status_code == 200, response.text
    return response.json()


async def _sent_back(client: AsyncClient, db_session: AsyncSession, admin: str) -> str:
    """``rosa`` sent back to ``registered`` with an active review request."""
    rosa = await create_registered_user(db_session, "rosa")
    await attach_identity_document(db_session, "rosa")
    submitted = await client.post("/v1/users/me/submit-for-review", headers=auth(rosa))
    assert submitted.status_code == 200, submitted.text
    sent_back = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        json={"reason": "Please fix your date of birth"},
        headers=auth(admin),
    )
    assert sent_back.status_code == 201, sent_back.text
    await db_session.commit()
    return rosa


@pytest.mark.requirement("users:R8a")
@pytest.mark.asyncio
async def test_should_report_username_taken_when_its_user_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )
    await db_session.commit()

    response = await client.get(
        "/v1/auth/username-available", params={"username": "gone"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["available"] is False


@pytest.mark.requirement("users:R9a")
@pytest.mark.asyncio
async def test_should_reject_reapply_when_date_of_birth_is_not_utc_midnight(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    rosa = await _sent_back(client, db_session, admin)

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        json={"dateOfBirthUtc": MIDNIGHT_2000 + 3_600_000},
        headers=auth(rosa),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    private = await client.get("/v1/users/by_id/rosa/private", headers=auth(admin))
    assert private.status_code == 200, private.text
    assert private.json()["dateOfBirthUtc"] is None
    assert private.json()["adminReviewNote"] == "Please fix your date of birth"


@pytest.mark.requirement("users:R26")
@pytest.mark.asyncio
async def test_should_refuse_block_when_user_is_already_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(db_session, "bo", UserStatus.blocked)
    await db_session.commit()

    response = await client.post("/v1/users/by_id/bo/block", headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "ALREADY_BLOCKED"
    assert (await _profile(client, admin, "bo"))["status"] == "blocked"


@pytest.mark.requirement("users:R26")
@pytest.mark.asyncio
async def test_should_refuse_unblock_when_user_is_not_blocked(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()

    response = await client.post("/v1/users/by_id/amy/unblock", headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "NOT_BLOCKED"
    assert (await _profile(client, admin, "amy"))["status"] == "active"


@pytest.mark.requirement("users:R28")
@pytest.mark.asyncio
async def test_should_refuse_mark_left_when_user_already_left(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(db_session, "lea", UserStatus.left)
    await db_session.commit()

    response = await client.post("/v1/users/by_id/lea/mark-left", headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "ALREADY_LEFT"
    assert (await _profile(client, admin, "lea"))["status"] == "left"


@pytest.mark.requirement("users:R28")
@pytest.mark.asyncio
async def test_should_refuse_reactivate_when_user_did_not_leave(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(db_session, "bo", UserStatus.blocked)
    await db_session.commit()

    response = await client.post("/v1/users/by_id/bo/reactivate", headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "NOT_LEFT"
    assert (await _profile(client, admin, "bo"))["status"] == "blocked"


@pytest.mark.requirement("users:R31a")
@pytest.mark.asyncio
async def test_should_grant_nothing_when_super_admin_is_given_as_a_role(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    amy = await create_member_user(db_session, "amy")
    await db_session.commit()

    assigned = await client.post(
        "/v1/users/by_id/amy/roles", json={"role": "super_admin"}, headers=auth(admin)
    )
    assert assigned.status_code == 422, assigned.text

    profile = await _profile(client, admin, "amy")
    assert profile["roles"] == []
    assert profile["isSuperAdmin"] is False

    feed = await client.get("/v1/audit_log", headers=auth(amy))
    assert feed.status_code == 403, feed.text


@pytest.mark.requirement("users:R40a")
@pytest.mark.asyncio
async def test_should_create_active_user_when_admin_names_no_status(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    created = await client.post(
        "/v1/users",
        json={
            "username": "newbie",
            "passwordHash": "SecurePass123",
            "firstName": "New",
            "gender": "female",
            "dateOfBirthUtc": MIDNIGHT_2000,
            "phone": "9876543210",
        },
        headers=auth(admin),
    )

    assert created.status_code == 201, created.text
    assert created.json()["status"] == "active"
    assert (await _profile(client, admin, "newbie"))["status"] == "active"


@pytest.mark.requirement("users:R53a")
@pytest.mark.asyncio
async def test_should_refuse_soft_delete_when_user_is_already_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(
        db_session, "gone", UserStatus.active, deleted=True
    )
    await db_session.commit()

    response = await client.delete("/v1/users/by_id/gone", headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "ALREADY_DELETED"
    deleted = await client.get("/v1/users/deleted", headers=auth(admin))
    assert [u["username"] for u in deleted.json()["items"]] == ["gone"]


@pytest.mark.requirement("users:R56a")
@pytest.mark.asyncio
async def test_should_refuse_former_super_admin_when_standing_is_handed_over(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")
    await db_session.commit()

    handed = await client.post(
        "/v1/users/by_id/ra/transfer-superadmin", headers=auth(admin)
    )
    assert handed.status_code == 200, handed.text

    assert (await _profile(client, admin, "admin"))["isSuperAdmin"] is False
    feed = await client.get("/v1/audit_log", headers=auth(admin))
    assert feed.status_code == 403, feed.text


@pytest.mark.requirement("users:R58")
@pytest.mark.asyncio
async def test_should_refuse_transfer_when_super_admin_names_themselves(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/admin/transfer-superadmin", headers=auth(admin)
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "CANNOT_TRANSFER_TO_SELF"
    assert (await _profile(client, admin, "admin"))["isSuperAdmin"] is True


@pytest.mark.requirement("users:R59a")
@pytest.mark.asyncio
async def test_should_refuse_mark_left_when_target_is_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ra")
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/admin/mark-left", headers=auth(regular)
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_PROTECTION"
    assert (await _profile(client, regular, "admin"))["status"] == "active"
