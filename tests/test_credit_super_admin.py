"""The super admin cannot hold credit (#519).

The super admin is a housekeeping account, not a member: no credit account
is opened for them, whoever asks. Rule credit:R18b in
``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_member, days_from_now, open_account
from .helpers import create_admin_user, create_regular_admin_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


def _opening(membername: str) -> dict:
    return {
        "membername": membername,
        "credits": 5,
        "validFromUtc": days_from_now(-1),
        "validUntilUtc": days_from_now(30),
        "reason": "Package purchased",
    }


async def _accounts_of(client: AsyncClient, token: str, membername: str) -> list:
    listed = await client.get(
        "/v1/credits/accounts", params={"membername": membername}, headers=auth(token)
    )
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R18b")
async def test_should_refuse_account_when_super_admin_opens_one_for_themselves(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/v1/credits/accounts", json=_opening("admin"), headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_CANNOT_HOLD_CREDIT"
    assert await _accounts_of(client, admin, "admin") == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R18b")
async def test_should_refuse_account_when_regular_admin_opens_one_for_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ra")
    await db_session.commit()

    response = await client.post(
        "/v1/credits/accounts", json=_opening("admin"), headers=auth(regular)
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "SUPER_ADMIN_CANNOT_HOLD_CREDIT"
    assert await _accounts_of(client, regular, "admin") == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R18a")
async def test_should_open_account_when_regular_admin_is_the_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")
    await create_member(db_session, "alice")
    await db_session.commit()

    for_admin = await open_account(client, admin, "ra", credits=4)
    for_member = await open_account(client, admin, "alice", credits=6)

    assert [a["accountId"] for a in await _accounts_of(client, admin, "ra")] == [
        for_admin["accountId"]
    ]
    assert [a["balance"] for a in await _accounts_of(client, admin, "alice")] == [6]
    assert for_member["membername"] == "alice"
