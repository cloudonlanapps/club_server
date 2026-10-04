"""Who reads a member's credit, and what they see (#500).

Covers credit R16, R64, R78, R86, R88 and R89 from
``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    create_event,
    days_from_now,
    drain_account,
    expire_account,
    get_account,
    open_account,
)
from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import auth

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def _get(client: AsyncClient, token: str, path: str, **params: object):
    response = await client.get(path, params=params, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _codes(rows: list[dict]) -> list[str]:
    return [row["accountId"] for row in rows]


# --- R16, R89: staff read any member's credit ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R16")
async def test_should_let_coach_search_a_members_accounts_and_ledger(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    _ = await create_member_user(db_session, "alice")
    opened = await open_account(client, admin, "alice", credits=6)

    accounts = await _get(client, coach, "/v1/credits/accounts", membername="alice")
    ledger = await _get(client, coach, "/v1/credits/entries", membername="alice")

    assert [(a["accountId"], a["balance"]) for a in accounts["items"]] == [
        (opened["accountId"], 6)
    ]
    assert [(e["accountId"], e["entryType"], e["amount"]) for e in ledger["items"]] == [
        (opened["accountId"], "grant", 6)
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R89")
async def test_should_show_coach_the_same_credit_view_the_member_sees(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    alice = await create_member_user(db_session, "alice")
    _ = await open_account(client, admin, "alice", credits=6)
    expired = await open_account(client, admin, "alice", credits=2)
    await expire_account(db_session, str(expired["accountId"]))
    await db_session.commit()

    for path in ("/v1/mycredits/by_id/alice", "/v1/mycredits/by_id/alice/entries"):
        as_member = await _get(client, alice, path)
        as_coach = await _get(client, coach, path)
        assert as_coach == as_member, path
    listed = await _get(client, coach, "/v1/mycredits/by_id/alice")
    assert sorted(a["state"] for a in listed) == ["expired", "usable"]


# --- R78: nobody else's member view of the credit ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R78")
@pytest.mark.requirement("credit:R15")
async def test_should_refuse_another_member_reading_a_members_credit(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    opened = await open_account(client, admin, "alice", credits=6)
    own = await _get(client, alice, "/v1/mycredits/by_id/alice")
    assert _codes(own) == [opened["accountId"]]

    for path in ("/v1/mycredits/by_id/alice", "/v1/mycredits/by_id/alice/entries"):
        response = await client.get(path, headers=auth(bob))
        assert response.status_code == 403, (path, response.text)


# --- R86: a member's own accounts ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R86")
async def test_should_show_member_balance_window_binding_and_usability(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    event_id = await create_event(client, admin)
    until = days_from_now(30)
    general = await open_account(client, admin, "alice", credits=6, valid_until=until)
    bound = await open_account(client, admin, "alice", credits=4, event_id=event_id)

    listed = await _get(client, alice, "/v1/mycredits/by_id/alice")

    shown = {
        a["accountId"]: (a["kind"], a["eventId"], a["balance"], a["usable"])
        for a in listed
    }
    assert shown == {
        general["accountId"]: ("general", None, 6, True),
        bound["accountId"]: ("event", event_id, 4, True),
    }
    by_code = {a["accountId"]: a for a in listed}
    assert by_code[general["accountId"]]["validUntilUtc"] == until
    assert by_code[general["accountId"]]["validFromUtc"] == general["validFromUtc"]


# --- R64, R88: expired and empty accounts are shown, described as such ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R64")
async def test_should_describe_expired_balance_as_expired_to_admin_and_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    opened = await open_account(client, admin, "alice", credits=5)
    code = str(opened["accountId"])
    await expire_account(db_session, code)
    await db_session.commit()

    as_admin = await get_account(client, admin, code)
    (as_member,) = await _get(client, alice, "/v1/mycredits/by_id/alice")

    for view in (as_admin, as_member):
        assert (view["accountId"], view["balance"], view["state"], view["usable"]) == (
            code,
            5,
            "expired",
            False,
        )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R88")
async def test_should_list_expired_and_empty_accounts_beside_live_ones(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    live = await open_account(client, admin, "alice", credits=5)
    empty = await open_account(client, admin, "alice", credits=3)
    await drain_account(client, admin, str(empty["accountId"]))
    expired = await open_account(client, admin, "alice", credits=2)
    await expire_account(db_session, str(expired["accountId"]))
    await db_session.commit()
    closed = await open_account(client, admin, "alice", credits=1)
    drained = await client.post(
        f"/v1/credits/accounts/{closed['accountId']}/transfer",
        json={
            "penalty": 1,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Forfeited",
        },
        headers=auth(admin),
    )
    assert drained.status_code == 200, drained.text

    listed = await _get(client, alice, "/v1/mycredits/by_id/alice")
    with_closed = await _get(
        client, alice, "/v1/mycredits/by_id/alice", includeClosed="true"
    )

    assert {a["accountId"]: a["state"] for a in listed} == {
        live["accountId"]: "usable",
        empty["accountId"]: "empty",
        expired["accountId"]: "expired",
    }
    assert {a["accountId"]: a["state"] for a in with_closed} == {
        live["accountId"]: "usable",
        empty["accountId"]: "empty",
        expired["accountId"]: "expired",
        closed["accountId"]: "closed",
    }
