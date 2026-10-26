"""The club's profile options: positions and languages (#92, users R69–R69c).

Read by every signed-in user (the onboarding step needs them before
approval), written by admins, checked on write.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .extended_profile_helpers import (
    ADMIN_PROFILE_OPTIONS,
    OPTIONS,
    PROFILE_OPTIONS,
    auth,
)
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
    create_user_with_status,
)

EMPTY: dict[str, Any] = {"positions": [], "languages": []}


async def read_options(client: AsyncClient, token: str) -> dict[str, Any]:
    response = await client.get(PROFILE_OPTIONS, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.requirement("users:R69")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reader", ["admin", "coach", "member", "registered", "pending"]
)
async def test_should_read_empty_lists_when_options_were_never_written(
    client: AsyncClient, db_session: AsyncSession, reader: str
):
    tokens = {
        "admin": await create_admin_user(db_session),
        "coach": await create_coach_user(db_session, "cora"),
        "member": await create_member_user(db_session, "mira"),
        "registered": await create_user_with_status(
            db_session, "reg", UserStatus.registered
        ),
        "pending": await create_user_with_status(db_session, "pen", UserStatus.pending),
    }

    assert await read_options(client, tokens[reader]) == EMPTY


@pytest.mark.requirement("users:R69")
@pytest.mark.asyncio
async def test_should_refuse_profile_options_read_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.get(PROFILE_OPTIONS)

    assert response.status_code == 401, response.text


@pytest.mark.requirement("users:R69a")
@pytest.mark.asyncio
@pytest.mark.parametrize("admin_kind", ["super", "regular"])
async def test_should_store_profile_options_when_admin_writes_them(
    client: AsyncClient,
    db_session: AsyncSession,
    extended_profile_enabled: None,
    admin_kind: str,
):
    super_admin = await create_admin_user(db_session)
    admin = (
        super_admin
        if admin_kind == "super"
        else await create_regular_admin_user(db_session, "ravi")
    )
    member = await create_member_user(db_session, "mira")
    pending = await create_user_with_status(db_session, "pen", UserStatus.pending)

    written = await client.put(PROFILE_OPTIONS, json=OPTIONS, headers=auth(admin))

    assert written.status_code == 200, written.text
    assert written.json() == OPTIONS
    assert await read_options(client, member) == OPTIONS
    assert await read_options(client, pending) == OPTIONS


@pytest.mark.requirement("users:R69a")
@pytest.mark.asyncio
async def test_should_audit_a_profile_options_write(
    client: AsyncClient, db_session: AsyncSession, extended_profile_enabled: None
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ravi")

    written = await client.put(PROFILE_OPTIONS, json=OPTIONS, headers=auth(regular))

    assert written.status_code == 200, written.text
    log = await client.get("/v1/audit_log", headers=auth(admin))
    assert log.status_code == 200, log.text
    rows = log.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["action"] == "update_system_preference"
    assert rows[0]["actor"]["username"] == "ravi"
    assert rows[0]["details"]["key"] == "profile_options"
    assert rows[0]["details"]["previousValue"] is None


@pytest.mark.requirement("users:R69a")
@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["coach", "member"])
async def test_should_refuse_profile_options_write_when_caller_is_not_an_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    extended_profile_enabled: None,
    writer: str,
):
    admin = await create_admin_user(db_session)
    token = (
        await create_coach_user(db_session, "cora")
        if writer == "coach"
        else await create_member_user(db_session, "mira")
    )
    await db_session.commit()

    response = await client.put(PROFILE_OPTIONS, json=OPTIONS, headers=auth(token))

    assert response.status_code == 403, response.text
    assert await read_options(client, admin) == EMPTY


@pytest.mark.requirement("users:R69a")
@pytest.mark.asyncio
async def test_should_refuse_profile_options_write_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession, extended_profile_enabled: None
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.put(PROFILE_OPTIONS, json=OPTIONS)

    assert response.status_code == 401, response.text
    assert await read_options(client, admin) == EMPTY


INVALID_OPTIONS: list[Any] = [
    {"positions": [{"value": "", "label": {"en": "Blank"}}], "languages": []},
    {"positions": [{"value": "Forward", "label": {}}], "languages": []},
    {"positions": [{"value": "Forward", "label": {"en": ""}}], "languages": []},
    {
        "positions": [
            {"value": "Forward", "label": {"en": "Forward"}},
            {"value": "Forward", "label": {"en": "Striker"}},
        ],
        "languages": [],
    },
    {"positions": [], "languages": ["English", ""]},
    {"positions": [], "languages": ["English", "English"]},
    {"positions": []},
    {"positions": [], "languages": [], "tiers": ["Gold"]},
    {"positions": [{"value": "Forward"}], "languages": []},
    ["Forward"],
]


@pytest.mark.requirement("users:R69b")
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", INVALID_OPTIONS)
async def test_should_refuse_profile_options_when_the_document_is_invalid(
    client: AsyncClient,
    db_session: AsyncSession,
    extended_profile_enabled: None,
    bad: Any,
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.put(PROFILE_OPTIONS, json=bad, headers=auth(admin))

    assert response.status_code == 422, response.text
    assert await read_options(client, admin) == EMPTY


@pytest.mark.requirement("users:R69b")
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", INVALID_OPTIONS)
async def test_should_refuse_profile_options_when_written_as_a_generic_preference(
    client: AsyncClient, db_session: AsyncSession, bad: Any
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.patch(
        ADMIN_PROFILE_OPTIONS, json={"value": bad}, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_PREFERENCE_VALUE"
    assert await read_options(client, admin) == EMPTY


@pytest.mark.requirement("users:R69b")
@pytest.mark.asyncio
async def test_should_store_profile_options_when_written_as_a_generic_preference(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")

    response = await client.patch(
        ADMIN_PROFILE_OPTIONS, json={"value": OPTIONS}, headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    assert await read_options(client, member) == OPTIONS


@pytest.mark.requirement("users:R69c")
@pytest.mark.asyncio
async def test_should_refuse_profile_options_write_when_extended_profile_is_off(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await db_session.commit()

    response = await client.put(PROFILE_OPTIONS, json=OPTIONS, headers=auth(admin))

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "EXTENDED_PROFILE_DISABLED"
    assert await read_options(client, member) == EMPTY
