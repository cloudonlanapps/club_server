"""Transferring a credit balance out of an account (#294).

One operation serves three stories — expiry disposition, voluntary
drop-out and admin removal — differing only in the penalty. Covers
R63, R65-R69 and R75 from ``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    create_event,
    create_member,
    days_from_now,
    get_account,
    open_account,
)
from .helpers import create_admin_user, create_coach_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


# --- Transfer: the one mechanic behind expiry, drop-out and removal ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R65a")
@pytest.mark.requirement("credit:R66")
async def test_should_open_new_general_account_when_balance_transferred(
    client: AsyncClient, db_session: AsyncSession
):
    """R65/R65a: a transfer creates the account it moves into."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    source = await open_account(
        client, admin_token, "alice", credits=20, event_id=event_id
    )

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Member dropped out",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is not None
    assert body["created"]["kind"] == "general"
    assert body["created"]["balance"] == 15
    assert body["created"]["accountId"] != source["accountId"]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R65a")
async def test_should_close_source_account_when_balance_transferred(
    client: AsyncClient, db_session: AsyncSession
):
    """R65a: the transfer closes the account it drains."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    source = await open_account(
        client, admin_token, "alice", credits=20, event_id=event_id
    )

    _ = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Member dropped out",
        },
        headers=auth(admin_token),
    )

    refreshed = await get_account(client, admin_token, source["accountId"])
    assert refreshed["state"] == "closed"
    assert refreshed["balance"] == 0
    assert refreshed["closedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R67")
async def test_should_transfer_whole_balance_when_penalty_is_zero(
    client: AsyncClient, db_session: AsyncSession
):
    """R67: a zero penalty is a valid and expected case."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    source = await open_account(
        client, admin_token, "alice", credits=20, event_id=event_id
    )

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "No penalty on medical withdrawal",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["created"]["balance"] == 20


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R68")
async def test_should_open_no_account_when_penalty_consumes_the_balance(
    client: AsyncClient, db_session: AsyncSession
):
    """R68: nothing survives, so nothing is opened."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    source = await open_account(
        client, admin_token, "alice", credits=10, event_id=event_id
    )

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 10,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Removed for conduct",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["created"] is None
    refreshed = await get_account(client, admin_token, source["accountId"])
    assert refreshed["state"] == "closed"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R69")
async def test_should_cap_penalty_at_balance_when_penalty_is_larger(
    client: AsyncClient, db_session: AsyncSession
):
    """R69: an oversized penalty stops at zero and spills nowhere."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    other = await open_account(client, admin_token, "alice", credits=30)
    source = await open_account(
        client, admin_token, "alice", credits=10, event_id=event_id
    )

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 25,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Removed for conduct",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["created"] is None
    drained = await get_account(client, admin_token, source["accountId"])
    assert drained["balance"] == 0
    untouched = await get_account(client, admin_token, other["accountId"])
    assert untouched["balance"] == 30, "other accounts must not be consulted"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R75")
async def test_should_give_new_account_its_own_window_when_transferred(
    client: AsyncClient, db_session: AsyncSession
):
    """R75: the new account does not inherit the closed one's window."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    new_until = days_from_now(200)
    source = await open_account(
        client,
        admin_token,
        "alice",
        credits=10,
        event_id=event_id,
        valid_until=days_from_now(20),
    )

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": new_until,
            "reason": "Dropped out",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["created"]["validUntilUtc"] == new_until


@pytest.mark.asyncio
async def test_should_reject_transfer_when_account_already_closed(
    client: AsyncClient, db_session: AsyncSession
):
    """An account is drained once; a second transfer has nothing to move."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    source = await open_account(
        client, admin_token, "alice", credits=10, event_id=event_id
    )
    body = {
        "penalty": 0,
        "validFromUtc": days_from_now(-1),
        "validUntilUtc": days_from_now(90),
        "reason": "Dropped out",
    }
    first = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json=body,
        headers=auth(admin_token),
    )
    assert first.status_code == 200

    second = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json=body,
        headers=auth(admin_token),
    )

    assert second.status_code == 422
    assert second.json()["detail"]["code"] == "ACCOUNT_CLOSED"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R14")
async def test_should_reject_transfer_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R14: transferring is an admin power."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    source = await open_account(
        client, admin_token, "alice", credits=10, event_id=event_id
    )

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Dropped out",
        },
        headers=auth(coach_token),
    )

    assert response.status_code == 403
