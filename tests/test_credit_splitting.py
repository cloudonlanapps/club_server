"""Splitting a session cost across accounts (#294, R16, R29, R30).

Unreachable in production today, because every programme costs one credit
per session (R33) and one credit never needs splitting. The path exists
because the rules are written in terms of the cost returned by
``session_cost_for`` rather than a literal, so that giving events their own
price later is a change to that function alone (R34).

These tests raise the cost so the path actually executes. Without them it
would be code that has never run.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services import credit_charge

from .credit_helpers import auth, balance_of, create_member, open_account
from .helpers import create_admin_user
from .test_attendance import create_venue, future_time_ms, move_event_to_recent_past

pytestmark = pytest.mark.usefixtures("credit_enabled")


@pytest.fixture
def session_costs_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Charge three credits per session instead of one."""
    monkeypatch.setattr(credit_charge, "session_cost_for", lambda _event: 3)
    return None


async def programme_with_member(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, username: str
) -> tuple[int, int]:
    """A past programme occurrence with ``username`` assigned."""
    venue_id = await create_venue(client, admin_token)
    start = future_time_ms(24)
    response = await client.post(
        "/v1/events",
        json={
            "title": "Split Programme",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": start + 60 * 60 * 1000,
        },
        headers=auth(admin_token),
    )
    event_id = response.json()["id"]
    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert assign.status_code in (200, 204), assign.text
    occurrence = await move_event_to_recent_past(db_session, event_id, [username])
    return event_id, occurrence


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R27")
@pytest.mark.requirement("credit:R29")
@pytest.mark.requirement("credit:R32")
@pytest.mark.requirement("credit:R34")
async def test_should_split_cost_across_accounts_when_one_cannot_cover_it(
    client: AsyncClient, db_session: AsyncSession, session_costs_three: None
):
    """R29: the cost is met from accounts in selection order until covered."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    first = await open_account(client, admin_token, "alice", credits=2)
    second = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert marked.status_code == 200, marked.text
    assert await balance_of(client, admin_token, first["accountId"]) == 0
    assert await balance_of(client, admin_token, second["accountId"]) == 4


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R30")
@pytest.mark.requirement("credit:R34")
async def test_should_take_nothing_when_accounts_cannot_meet_the_cost(
    client: AsyncClient, db_session: AsyncSession, session_costs_three: None
):
    """R30: the whole cost is taken or none of it is.

    A partial spend would leave the member paying for a session they were
    never marked for.
    """
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    first = await open_account(client, admin_token, "alice", credits=1)
    second = await open_account(client, admin_token, "alice", credits=1)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert marked.status_code == 200
    assert marked.json()["refused"][0]["code"] == "INSUFFICIENT_CREDIT"
    assert await balance_of(client, admin_token, first["accountId"]) == 1
    assert await balance_of(client, admin_token, second["accountId"]) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R34")
@pytest.mark.requirement("credit:R49")
async def test_should_refund_every_part_when_a_split_charge_is_reversed(
    client: AsyncClient, db_session: AsyncSession, session_costs_three: None
):
    """R49: a refund returns to each account in the amount it paid."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    first = await open_account(client, admin_token, "alice", credits=2)
    second = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance/alice",
        headers=auth(admin_token),
    )

    assert cleared.status_code == 204, cleared.text
    assert await balance_of(client, admin_token, first["accountId"]) == 2
    assert await balance_of(client, admin_token, second["accountId"]) == 5


@pytest.mark.asyncio
async def test_should_record_one_statement_entry_per_account_in_a_split(
    client: AsyncClient, db_session: AsyncSession, session_costs_three: None
):
    """R87: the statement adds up to what was actually taken."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    _ = await open_account(client, admin_token, "alice", credits=2)
    _ = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    statement = await client.get(
        "/v1/mycredits/by_id/alice/entries", headers=auth(member_token)
    )

    deductions = [
        row
        for row in statement.json()["items"]
        if row["entryType"] == "sessionDeduction"
    ]
    assert len(deductions) == 2
    assert sorted(row["amount"] for row in deductions) == [-2, -1]
