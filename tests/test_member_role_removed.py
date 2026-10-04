"""The ``member`` role is gone (#400).

It gated nothing and was never granted automatically, so it survived only
as a label an admin could set. These pin that the enum no longer defines
it, that the role endpoints refuse it, and that the data migration strips
it from every stored roles list.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.member_role import strip_member_role
from club_server.db.models.user import Role, User

from .helpers import create_admin_user, create_member_user


@pytest.mark.requirement("users:R29")
def test_should_not_define_member_role():
    assert "member" not in {role.value for role in Role}
    assert {role.value for role in Role} == {"admin", "coach"}


@pytest.mark.requirement("users:R29")
@pytest.mark.asyncio
async def test_should_reject_member_when_assigning_role(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/alice/roles",
        json={"role": "member"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422

    check = await client.get(
        "/v1/users/by_id/alice", headers={"Authorization": f"Bearer {token}"}
    )
    assert check.status_code == 200
    assert check.json()["roles"] == []


@pytest.mark.requirement("users:R29")
@pytest.mark.asyncio
async def test_should_reject_member_when_removing_role(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.delete(
        "/v1/users/by_id/alice/roles/member",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def _run_strip(db: AsyncSession) -> int:
    raw = await db.connection()
    return await raw.run_sync(strip_member_role)


@pytest.mark.asyncio
async def test_should_strip_member_from_stored_roles_when_migrating(
    db_session: AsyncSession,
):
    _ = await create_member_user(db_session, "alice")
    _ = await create_member_user(db_session, "bob")
    _ = await create_member_user(db_session, "carol")
    await db_session.execute(
        text("UPDATE users SET roles = :r WHERE username = 'alice'"),
        {"r": json.dumps({"roles": ["member"]})},
    )
    await db_session.execute(
        text("UPDATE users SET roles = :r WHERE username = 'bob'"),
        {"r": json.dumps({"roles": ["coach", "member", "admin"]})},
    )
    await db_session.flush()

    changed = await _run_strip(db_session)
    assert changed == 2

    rows = (await db_session.execute(select(User.username, User.roles))).all()
    roles = {username: json.loads(raw)["roles"] for username, raw in rows}
    assert roles["alice"] == []
    assert roles["bob"] == ["coach", "admin"]
    assert roles["carol"] == []


@pytest.mark.asyncio
async def test_should_change_nothing_when_no_user_holds_member(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")

    changed = await _run_strip(db_session)
    assert changed == 0

    rows = (await db_session.execute(select(User.username, User.roles))).all()
    roles = {username: json.loads(raw)["roles"] for username, raw in rows}
    assert roles == {"admin": ["admin"], "alice": []}
