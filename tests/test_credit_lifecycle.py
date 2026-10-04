"""Extending, reversing and transferring credit accounts (#294).

Covers the correction rules (R58-R60), expiry (R61-R64) and leaving a
programme (R65-R69, R75) from ``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    expire_account,
    create_member,
    days_from_now,
    get_account,
    open_account,
)
from .helpers import create_admin_user, create_coach_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


# --- Extending validity (R62, R64) ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R62")
async def test_should_make_expired_account_usable_when_validity_extended(
    client: AsyncClient, db_session: AsyncSession
):
    """R62: extending the window is the only way back from expiry."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=5)
    account_id = account["accountId"]
    await expire_account(db_session, account_id)
    account = await get_account(client, admin_token, account_id)
    assert account["state"] == "expired"
    assert account["usable"] is False

    response = await client.post(
        f"/v1/credits/accounts/{account_id}/extend",
        json={"validUntilUtc": days_from_now(30), "reason": "Goodwill extension"},
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    refreshed = await get_account(client, admin_token, account_id)
    assert refreshed["state"] == "usable"
    assert refreshed["usable"] is True
    assert refreshed["balance"] == 5


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R11")
@pytest.mark.requirement("credit:R61")
async def test_should_keep_balance_when_account_expires(
    client: AsyncClient, db_session: AsyncSession
):
    """R61: expired credits are never destroyed or swept."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=12)
    await expire_account(db_session, account["accountId"])

    refreshed = await get_account(client, admin_token, account["accountId"])

    assert refreshed["balance"] == 12
    assert refreshed["state"] == "expired"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R62")
async def test_should_reject_extension_when_new_end_is_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    """R62: an extension moves the end date forwards, not backwards."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(
        client, admin_token, "alice", valid_until=days_from_now(60)
    )

    response = await client.post(
        f"/v1/credits/accounts/{account['accountId']}/extend",
        json={"validUntilUtc": days_from_now(10), "reason": "Shorten"},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_VALIDITY_WINDOW"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R14")
async def test_should_reject_extension_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R14: adjusting an account is an admin power."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice")

    response = await client.post(
        f"/v1/credits/accounts/{account['accountId']}/extend",
        json={"validUntilUtc": days_from_now(120), "reason": "Goodwill"},
        headers=auth(coach_token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R79")
@pytest.mark.requirement("credit:R81")
async def test_should_record_extension_on_statement_when_validity_extended(
    client: AsyncClient, db_session: AsyncSession
):
    """R81: a validity change belongs on the statement, at amount zero."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=5)
    account_id = account["accountId"]

    _ = await client.post(
        f"/v1/credits/accounts/{account_id}/extend",
        json={"validUntilUtc": days_from_now(120), "reason": "Goodwill extension"},
        headers=auth(admin_token),
    )

    entries = await client.get(
        "/v1/credits/entries",
        params={"accountId": account_id, "entryType": "validityExtended"},
        headers=auth(admin_token),
    )
    assert entries.status_code == 200
    items = entries.json()["items"]
    assert len(items) == 1
    assert items[0]["amount"] == 0
    assert items[0]["reason"] == "Goodwill extension"


# --- Reversing a mistaken grant (R58, R59) ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R11")
@pytest.mark.requirement("credit:R58")
async def test_should_empty_account_when_whole_grant_reversed(
    client: AsyncClient, db_session: AsyncSession
):
    """R58: an admin undoes their own mistake in full."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    account_id = account["accountId"]

    response = await client.post(
        f"/v1/credits/accounts/{account_id}/reverse",
        json={"reason": "Opened for the wrong member"},
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    refreshed = await get_account(client, admin_token, account_id)
    assert refreshed["balance"] == 0
    assert refreshed["state"] == "empty"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R58")
async def test_should_reduce_balance_when_part_of_grant_reversed(
    client: AsyncClient, db_session: AsyncSession
):
    """R58: a partial reversal corrects a wrong amount."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    account_id = account["accountId"]

    response = await client.post(
        f"/v1/credits/accounts/{account_id}/reverse",
        json={"credits": 4, "reason": "Granted four too many"},
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    refreshed = await get_account(client, admin_token, account_id)
    assert refreshed["balance"] == 6


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R59")
async def test_should_reject_reversal_when_it_exceeds_the_balance(
    client: AsyncClient, db_session: AsyncSession
):
    """R59: a grant cannot be reversed beyond what remains unspent."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=3)

    response = await client.post(
        f"/v1/credits/accounts/{account['accountId']}/reverse",
        json={"credits": 5, "reason": "Too much"},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INSUFFICIENT_BALANCE"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R12")
async def test_should_never_produce_negative_balance_when_reversed_twice(
    client: AsyncClient, db_session: AsyncSession
):
    """R12: a balance can never go below zero."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=5)
    account_id = account["accountId"]

    first = await client.post(
        f"/v1/credits/accounts/{account_id}/reverse",
        json={"credits": 5, "reason": "Wrong member"},
        headers=auth(admin_token),
    )
    assert first.status_code == 200

    second = await client.post(
        f"/v1/credits/accounts/{account_id}/reverse",
        json={"credits": 1, "reason": "Again"},
        headers=auth(admin_token),
    )

    assert second.status_code == 422
    refreshed = await get_account(client, admin_token, account_id)
    assert refreshed["balance"] == 0
