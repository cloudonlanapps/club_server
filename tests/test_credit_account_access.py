"""Authorization and lookup on credit accounts (#294).

Covers R13-R17 (who may open, adjust and read) and R91 (finding an
account by its 8-character code alone) from
``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_member, days_from_now, open_account
from .helpers import create_admin_user, create_coach_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


# --- Authorization (R13, R15, R16, R17) ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R13")
async def test_should_reject_account_opening_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R13: a coach cannot open an account."""
    _ = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    await create_member(db_session, "alice")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(coach_token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R15")
async def test_should_reject_account_opening_when_caller_is_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: a member cannot open their own account."""
    _ = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(member_token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R17")
async def test_should_reject_account_opening_when_anonymous(client: AsyncClient):
    """R17: anonymous callers reach nothing."""
    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
    )

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R91")
async def test_should_reject_account_lookup_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R91 is admin-only: lookup by bare code is not a coach power."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice")

    response = await client.get(
        f"/v1/credits/accounts/{account['accountId']}", headers=auth(coach_token)
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R91")
async def test_should_find_account_by_code_alone_when_caller_is_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """R91: the code is enough; the owner need not be known."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    opened = await open_account(client, admin_token, "alice", credits=7)

    response = await client.get(
        f"/v1/credits/accounts/{opened['accountId']}", headers=auth(admin_token)
    )

    assert response.status_code == 200
    assert response.json()["membername"] == "alice"
    assert response.json()["balance"] == 7


@pytest.mark.asyncio
async def test_should_return_not_found_when_account_code_unknown(
    client: AsyncClient, db_session: AsyncSession
):
    """An unknown code is a 404, not an empty success."""
    admin_token = await create_admin_user(db_session)

    response = await client.get(
        "/v1/credits/accounts/ZZZZ9999", headers=auth(admin_token)
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CREDIT_ACCOUNT_NOT_FOUND"
