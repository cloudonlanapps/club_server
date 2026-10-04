"""Admin account operations whose rules were settled in #513, #514, #522, #523.

Marking a user left, the roles an admin may give or take, the status an
admin-created account starts in, and soft-deleting a deleted user. Each
test names the rule it proves in ``docs/users_requirements.md``.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.member_role import strip_role
from club_server.db.models.user import User, UserStatus

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
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


def _new_user(**extra: object) -> dict:
    return {
        "username": "newbie",
        "passwordHash": "SecurePass123",
        "firstName": "New",
        "gender": "female",
        "dateOfBirthUtc": MIDNIGHT_2000,
        "phone": "9876543210",
        **extra,
    }


# --- Soft-deleting a deleted user (#523) ---


@pytest.mark.requirement("users:R53a")
@pytest.mark.asyncio
async def test_should_return_404_when_soft_deleting_an_unknown_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.delete("/v1/users/by_id/nobody", headers=auth(admin))

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("users:R53a")
@pytest.mark.asyncio
async def test_should_refuse_second_soft_delete_when_regular_admin_repeats_it(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ra")
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()
    first = await client.delete("/v1/users/by_id/amy", headers=auth(regular))
    assert first.status_code == 200, first.text
    deleted_at = first.json()["deletedAtUtc"]
    assert deleted_at is not None

    second = await client.delete("/v1/users/by_id/amy", headers=auth(regular))

    assert second.status_code == 422, second.text
    assert second.json()["detail"]["code"] == "ALREADY_DELETED"
    assert (await _profile(client, regular, "amy"))["deletedAtUtc"] == deleted_at


# --- Admin creation creates active users only (#522) ---


@pytest.mark.requirement("users:R40a")
@pytest.mark.asyncio
async def test_should_create_active_user_when_admin_names_active_status(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    created = await client.post(
        "/v1/users", json=_new_user(status="active"), headers=auth(admin)
    )

    assert created.status_code == 201, created.text
    assert created.json()["status"] == "active"
    assert (await _profile(client, admin, "newbie"))["status"] == "active"


@pytest.mark.requirement("users:R40b")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested", ["activ", "registered", "pending", "blocked", "left"]
)
async def test_should_reject_admin_creation_when_status_is_not_active(
    client: AsyncClient, db_session: AsyncSession, requested: str
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/v1/users", json=_new_user(status=requested), headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    missing = await client.get("/v1/users/by_id/newbie", headers=auth(admin))
    assert missing.status_code == 404, missing.text


@pytest.mark.requirement("users:R40b")
@pytest.mark.asyncio
async def test_should_reject_admin_creation_when_regular_admin_names_left_status(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ra")
    await db_session.commit()

    response = await client.post(
        "/v1/users", json=_new_user(status="left"), headers=auth(regular)
    )

    assert response.status_code == 422, response.text
    missing = await client.get("/v1/users/by_id/newbie", headers=auth(regular))
    assert missing.status_code == 404, missing.text


# --- Only an active user can be marked left (#513) ---


@pytest.mark.requirement("users:R27a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_status", [UserStatus.registered, UserStatus.pending, UserStatus.blocked]
)
async def test_should_refuse_mark_left_when_user_is_not_active(
    client: AsyncClient, db_session: AsyncSession, user_status: UserStatus
):
    admin = await create_admin_user(db_session)
    _ = await create_user_with_status(db_session, "val", user_status)
    await db_session.commit()

    response = await client.post("/v1/users/by_id/val/mark-left", headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    assert (await _profile(client, admin, "val"))["status"] == user_status.value


@pytest.mark.requirement("users:R27")
@pytest.mark.asyncio
async def test_should_mark_active_user_left_when_regular_admin_asks(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ra")
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()

    response = await client.post("/v1/users/by_id/amy/mark-left", headers=auth(regular))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "left"
    assert (await _profile(client, regular, "amy"))["status"] == "left"


@pytest.mark.requirement("users:R32")
@pytest.mark.asyncio
async def test_should_refuse_mark_left_when_caller_is_coach_or_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    member = await create_member_user(db_session, "mo")
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()

    for token in (coach, member):
        response = await client.post(
            "/v1/users/by_id/amy/mark-left", headers=auth(token)
        )
        assert response.status_code == 403, response.text

    assert (await _profile(client, admin, "amy"))["status"] == "active"


# --- super_admin is not a role (#514) ---


@pytest.mark.requirement("users:R31a")
@pytest.mark.asyncio
async def test_should_reject_super_admin_when_giving_a_role(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/amy/roles", json={"role": "super_admin"}, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    profile = await _profile(client, admin, "amy")
    assert profile["roles"] == []
    assert profile["isSuperAdmin"] is False


@pytest.mark.requirement("users:R31a")
@pytest.mark.asyncio
async def test_should_reject_super_admin_when_taking_a_role(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    await db_session.execute(
        text(
            'UPDATE users SET roles = \'{"roles": ["coach", "super_admin"]}\' '
            "WHERE username = 'amy'"
        )
    )
    await db_session.commit()

    response = await client.delete(
        "/v1/users/by_id/amy/roles/super_admin", headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert (await _profile(client, admin, "amy"))["roles"] == ["coach", "super_admin"]


@pytest.mark.requirement("users:R31a")
@pytest.mark.asyncio
async def test_should_reject_super_admin_role_when_regular_admin_gives_it_to_self(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ra")
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/ra/roles", json={"role": "super_admin"}, headers=auth(regular)
    )

    assert response.status_code == 422, response.text
    profile = await _profile(client, admin, "ra")
    assert profile["roles"] == ["admin"]
    assert profile["isSuperAdmin"] is False


@pytest.mark.asyncio
async def test_should_strip_super_admin_from_stored_roles_when_migrating(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    await db_session.execute(
        text("UPDATE users SET roles = :r WHERE username = 'amy'"),
        {"r": json.dumps({"roles": ["coach", "super_admin", "admin"]})},
    )
    await db_session.flush()
    raw = await db_session.connection()

    changed = await raw.run_sync(lambda conn: strip_role(conn, "super_admin"))

    assert changed == 1
    rows = (await db_session.execute(select(User.username, User.roles))).all()
    roles = {username: json.loads(value)["roles"] for username, value in rows}
    assert roles == {"admin": ["admin"], "amy": ["coach", "admin"], "bob": []}
