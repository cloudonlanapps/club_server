"""Expired credit: refunds into it, disposing of it, and leaving it alone (#500).

Covers credit R55, R57, R63, R72 and R73e from
``docs/credit_system_requirements.md``. Expired credit is never destroyed
(R61), so every path here ends with the balance still somewhere.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services.credit_sweep import sweep_credit_settlements

from .credit_helpers import days_from_now, expire_account, get_account, open_account
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    mark,
    notifications_for,
    terminate,
)

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def _accounts_of(client: AsyncClient, token: str, member: str) -> list[dict]:
    response = await client.get(
        "/v1/credits/accounts", params={"membername": member}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _refunded_into_expired_account(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str]:
    """Alice charged from a general account that then expires, then the mark cleared.

    Returns (admin token, account code).
    """
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    start = at(minutes=10)
    event = await create_programme(client, admin, venue, start=start)
    account = await open_account(client, admin, "alice", credits=10)
    code = str(account["accountId"])
    assert (await assign(client, admin, event["id"], "alice")).status_code == 204
    assert (await mark(client, admin, event["id"], start, "alice")).status_code == 200
    assert (await get_account(client, admin, code))["balance"] == 9
    await expire_account(db_session, code)
    await db_session.commit()

    cleared = await client.delete(
        f"/v1/events/by_id/{event['id']}/occurrences/{start}/attendance/alice",
        headers=auth(admin),
    )
    assert cleared.status_code == 204, cleared.text
    return admin, code


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R55")
async def test_should_refund_into_source_account_when_it_has_expired(
    client: AsyncClient, db_session: AsyncSession
):
    admin, code = await _refunded_into_expired_account(client, db_session)

    account = await get_account(client, admin, code)
    assert (account["balance"], account["state"], account["usable"]) == (
        10,
        "expired",
        False,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R57")
async def test_should_make_refunded_expired_account_usable_when_extended(
    client: AsyncClient, db_session: AsyncSession
):
    admin, code = await _refunded_into_expired_account(client, db_session)

    extended = await client.post(
        f"/v1/credits/accounts/{code}/extend",
        json={"validUntilUtc": days_from_now(60), "reason": "Refunded session"},
        headers=auth(admin),
    )

    assert extended.status_code == 200, extended.text
    account = await get_account(client, admin, code)
    assert (account["balance"], account["state"]) == (10, "usable")


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R63")
async def test_should_move_expired_balance_to_new_general_account_less_penalty(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    event = await create_programme(client, admin, venue, start=at(days=1))
    bound = await open_account(client, admin, "alice", credits=10, event_id=event["id"])
    code = str(bound["accountId"])
    await expire_account(db_session, code)
    await db_session.commit()

    response = await client.post(
        f"/v1/credits/accounts/{code}/transfer",
        json={
            "penalty": 2,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Package lapsed; moved with late fee",
        },
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    source = await get_account(client, admin, code)
    assert (source["balance"], source["state"]) == (0, "closed")
    created = await get_account(client, admin, response.json()["created"]["accountId"])
    assert (created["kind"], created["balance"], created["state"]) == (
        "general",
        8,
        "usable",
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R72")
async def test_should_leave_bound_balance_in_place_when_sweep_runs_on_live_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    event = await create_programme(client, admin, venue, start=at(days=1))
    bound = await open_account(client, admin, "alice", credits=10, event_id=event["id"])
    assert (await assign(client, admin, event["id"], "alice")).status_code == 204

    _ = await sweep_credit_settlements(db_session, at(days=5))
    await db_session.commit()

    accounts = await _accounts_of(client, admin, "alice")
    assert [
        (a["accountId"], a["kind"], a["balance"], a["state"]) for a in accounts
    ] == [(bound["accountId"], "event", 10, "usable")]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R73e")
async def test_should_keep_expired_bound_account_when_terminated_programme_releases(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    start = at(days=2)
    cutoff = start + 3 * DAY_MS
    event = await create_programme(client, admin, venue, start=start)
    bound = await open_account(
        client,
        admin,
        "alice",
        credits=10,
        event_id=event["id"],
        valid_until=days_from_now(4),
    )
    assert (await assign(client, admin, event["id"], "alice")).status_code == 204
    assert (await terminate(client, admin, event["id"], cutoff)).status_code == 200

    _ = await sweep_credit_settlements(db_session, cutoff + MINUTE_MS)
    await db_session.commit()

    accounts = await _accounts_of(client, admin, "alice")
    assert [
        (a["accountId"], a["kind"], a["balance"], a["closedAtUtc"]) for a in accounts
    ] == [(bound["accountId"], "event", 10, None)]
    assert await notifications_for(db_session, "alice", "credit.released") == []
