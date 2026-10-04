"""Account shape, selection and correction rules not proven elsewhere (#500).

Covers credit R3, R10, R24, R28, R60, R76, R77, R83 and the rejected-grant
half of R85 from ``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.credit_session_charge import CreditSessionCharge
from club_server.utils import now_utc_ms

from .credit_helpers import (
    days_from_now,
    entries_for,
    expire_account,
    get_account,
    open_account,
)
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    audit_rows,
    auth,
    create_programme,
    create_venue,
    mark,
)

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def _accounts_of(client: AsyncClient, token: str, member: str) -> list[dict]:
    """The staff search for one member's accounts."""
    response = await client.get(
        "/v1/credits/accounts", params={"membername": member}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _paying_account(client: AsyncClient, token: str, event_id: int) -> str:
    """The account the roster says would pay for the only member's next mark."""
    response = await client.get(
        f"/v1/events/by_id/{event_id}/credits", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert len(rows) == 1, rows
    return rows[0]["payingAccountId"]


async def _imminent_programme(client: AsyncClient, admin: str) -> tuple[int, int]:
    """A daily programme whose first occurrence starts in ten minutes."""
    venue = await create_venue(client, admin)
    start = at(minutes=10)
    programme = await create_programme(client, admin, venue, start=start)
    return programme["id"], start


# --- R3: no perpetual account ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R3")
async def test_should_reject_account_when_validity_window_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.post(
        "/v1/credits/accounts",
        json={"membername": "alice", "credits": 5, "reason": "Package purchased"},
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert await _accounts_of(client, admin, "alice") == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R3")
async def test_should_carry_the_stated_window_when_general_account_opened(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    valid_from = days_from_now(-1)
    valid_until = days_from_now(45)

    opened = await open_account(
        client, admin, "alice", valid_from=valid_from, valid_until=valid_until
    )

    account = await get_account(client, admin, str(opened["accountId"]))
    assert account["kind"] == "general"
    assert account["validFromUtc"] == valid_from
    assert account["validUntilUtc"] == valid_until


# --- R10, R28: the payer is derived from the accounts' state every time ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R10")
async def test_should_move_the_payer_when_the_oldest_account_expires(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    older = await open_account(client, admin, "alice", credits=5)
    newer = await open_account(client, admin, "alice", credits=5)
    event_id, _ = await _imminent_programme(client, admin)
    assert (await assign(client, admin, event_id, "alice")).status_code == 204
    assert await _paying_account(client, admin, event_id) == older["accountId"]

    await expire_account(db_session, str(older["accountId"]))
    await db_session.commit()

    assert await _paying_account(client, admin, event_id) == newer["accountId"]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R28")
async def test_should_charge_next_account_when_oldest_has_expired(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    older = await open_account(client, admin, "alice", credits=5)
    newer = await open_account(client, admin, "alice", credits=5)
    event_id, start = await _imminent_programme(client, admin)
    assert (await assign(client, admin, event_id, "alice")).status_code == 204
    await expire_account(db_session, str(older["accountId"]))
    await db_session.commit()

    marked = await mark(client, admin, event_id, start, "alice")

    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []
    assert (await get_account(client, admin, str(older["accountId"])))["balance"] == 5
    assert (await get_account(client, admin, str(newer["accountId"])))["balance"] == 4


# --- R24: the opening entry carries the admin's reason ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R24")
async def test_should_record_stated_reason_on_the_opening_entry(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")

    opened = await open_account(
        client, admin, "alice", credits=6, reason="Autumn package, paid cash"
    )

    grants = await entries_for(
        client, admin, accountId=opened["accountId"], entryType="grant"
    )
    assert [(g["amount"], g["reason"], g["actorUsername"]) for g in grants] == [
        (6, "Autumn package, paid cash", "admin")
    ]


# --- R60: every correction carries a reason ---


CORRECTIONS = [
    ("extend", {"validUntilUtc": days_from_now(200)}),
    ("reverse", {"credits": 1}),
    (
        "transfer",
        {
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
        },
    ),
]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R60")
@pytest.mark.parametrize(("operation", "body"), CORRECTIONS)
@pytest.mark.parametrize("reason", [None, ""])
async def test_should_reject_correction_when_reason_is_missing(
    client: AsyncClient,
    db_session: AsyncSession,
    operation: str,
    body: dict,
    reason: str | None,
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    opened = await open_account(client, admin, "alice", credits=5)
    code = str(opened["accountId"])
    payload = dict(body)
    if reason is not None:
        payload["reason"] = reason

    response = await client.post(
        f"/v1/credits/accounts/{code}/{operation}", json=payload, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    account = await get_account(client, admin, code)
    assert (account["balance"], account["state"]) == (5, "usable")
    assert account["validUntilUtc"] == opened["validUntilUtc"]
    assert [
        e["entryType"] for e in await entries_for(client, admin, accountId=code)
    ] == ["grant"]


# --- R76, R77: credit never moves between members ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R76")
async def test_should_open_transfer_destination_for_the_same_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    _ = await create_member_user(db_session, "bob")
    source = await open_account(client, admin, "alice", credits=8)

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Dropped out",
        },
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    created = response.json()["created"]
    assert created["membername"] == "alice"
    assert [a["accountId"] for a in await _accounts_of(client, admin, "alice")] == [
        source["accountId"],
        created["accountId"],
    ]
    assert await _accounts_of(client, admin, "bob") == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R76")
async def test_should_reject_transfer_when_it_names_another_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    _ = await create_member_user(db_session, "bob")
    source = await open_account(client, admin, "alice", credits=8)

    response = await client.post(
        f"/v1/credits/accounts/{source['accountId']}/transfer",
        json={
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Gift to a friend",
            "membername": "bob",
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    account = await get_account(client, admin, str(source["accountId"]))
    assert (account["balance"], account["state"]) == (8, "usable")
    assert await _accounts_of(client, admin, "bob") == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R77")
async def test_should_keep_reversal_and_new_grant_independent_when_credit_moved(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    _ = await create_member_user(db_session, "bob")
    alice = await open_account(client, admin, "alice", credits=10)

    reversed_ = await client.post(
        f"/v1/credits/accounts/{alice['accountId']}/reverse",
        json={"credits": 3, "reason": "Three sessions belong to Bob"},
        headers=auth(admin),
    )
    assert reversed_.status_code == 200, reversed_.text
    bob = await open_account(
        client, admin, "bob", credits=3, reason="Three sessions from Alice's package"
    )

    assert (await get_account(client, admin, str(alice["accountId"])))["balance"] == 7
    assert (await get_account(client, admin, str(bob["accountId"])))["balance"] == 3
    alice_grant, reversal = await entries_for(client, admin, membername="alice")
    (bob_grant,) = await entries_for(client, admin, membername="bob")
    assert reversal["entryType"] == "grantReversal"
    assert reversal["offsetsEntryId"] == alice_grant["id"]
    assert bob_grant["entryType"] == "grant"
    assert bob_grant["offsetsEntryId"] is None


# --- R83: one charge row per member and occurrence, in the schema ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R83")
async def test_should_refuse_second_charge_row_when_occurrence_already_charged(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    _ = await open_account(client, admin, "alice", credits=5)
    event_id, start = await _imminent_programme(client, admin)
    assert (await assign(client, admin, event_id, "alice")).status_code == 204
    assert (await mark(client, admin, event_id, start, "alice")).status_code == 200

    db_session.add(
        CreditSessionCharge(
            event_id=event_id,
            occurrence_time_utc=start,
            membername="alice",
            total=1,
            created_at=now_utc_ms(),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    count = await db_session.execute(
        select(func.count()).select_from(CreditSessionCharge)
    )
    assert count.scalar_one() == 1


# --- R85: a rejected grant records nothing ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R85")
async def test_should_write_no_entry_or_audit_row_when_grant_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert await entries_for(client, admin, membername="alice") == []
    assert await audit_rows(db_session, "open_credit_account") == []
