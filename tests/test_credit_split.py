"""Credit across a programme split (#500).

A programme keeps one event id for life; a split changes its timetable from
a date and moves no enrollment. Covers credit R7, R40 and R82a from
``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import entries_for, get_account, open_account
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    enrollment_of,
    mark,
    split,
)

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def _marked_then_split(
    client: AsyncClient, db_session: AsyncSession, credits: int
) -> tuple[str, int, int, str]:
    """Alice, holding ``credits`` bound credits, marked present, then the programme split.

    Returns (admin token, event id, the marked occurrence, account code).
    """
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    other_venue = await create_venue(client, admin, "Rink B")
    start = at(minutes=10)
    event_id = (await create_programme(client, admin, venue, start=start))["id"]
    account = await open_account(
        client, admin, "alice", credits=credits, event_id=event_id
    )
    code = str(account["accountId"])
    assert (await assign(client, admin, event_id, "alice")).status_code == 204
    assert (await mark(client, admin, event_id, start, "alice")).status_code == 200
    assert (await get_account(client, admin, code))["balance"] == credits - 1

    done = await split(
        client,
        admin,
        event_id,
        effectiveDateTimeUtc=start + 2 * DAY_MS,
        venueId=other_venue,
    )
    assert done.status_code == 200, done.text
    return admin, event_id, start, code


async def _roster_row(client: AsyncClient, token: str, event_id: int) -> dict:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/credits", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    (row,) = response.json()["items"]
    return row


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R7")
async def test_should_keep_bound_account_paying_when_programme_is_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, _, code = await _marked_then_split(client, db_session, 10)

    account = await get_account(client, admin, code)
    assert (account["eventId"], account["balance"], account["state"]) == (
        event_id,
        9,
        "usable",
    )
    row = await _roster_row(client, admin, event_id)
    assert (row["payingAccountId"], row["usableCredits"], row["boundCredits"]) == (
        code,
        9,
        9,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R40")
async def test_should_keep_member_enrolled_when_split_finds_them_out_of_credit(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, _, _ = await _marked_then_split(client, db_session, 1)

    assert await enrollment_of(client, admin, event_id, "alice") == "assigned"
    row = await _roster_row(client, admin, event_id)
    assert (row["membername"], row["usableCredits"], row["blocked"]) == (
        "alice",
        0,
        True,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R82a")
async def test_should_not_charge_again_when_occurrence_is_remarked_after_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, start, code = await _marked_then_split(client, db_session, 10)

    remarked = await mark(client, admin, event_id, start, "alice", "absent")

    assert remarked.status_code == 200, remarked.text
    assert remarked.json()["refused"] == []
    assert (await get_account(client, admin, code))["balance"] == 9
    deductions = await entries_for(
        client, admin, membername="alice", entryType="sessionDeduction"
    )
    assert [(d["eventId"], d["occurrenceTimeUtc"]) for d in deductions] == [
        (event_id, start)
    ]
