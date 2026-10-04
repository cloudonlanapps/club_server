"""Credit when the club cancels a session (#294, R48).

Cancelling an occurrence that was already marked must return the credit,
exactly as approved leave does. The window is real rather than theoretical:
attendance opens 30 minutes before the start and cancellation is allowed
while the occurrence is still future, so a register marked early and then
cancelled is an ordinary Tuesday.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, balance_of, create_member, days_from_now, open_account
from .helpers import create_admin_user
from .redesign_helpers import occurrence_version

pytestmark = pytest.mark.usefixtures("credit_enabled")

MINUTE_MS = 60 * 1000


async def imminent_programme(
    client: AsyncClient, admin_token: str, username: str, minutes: int = 10
) -> tuple[int, int]:
    """A programme starting shortly: markable now, still cancellable."""
    venue = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=auth(admin_token)
    )
    start = days_from_now(0) + minutes * MINUTE_MS
    event = await client.post(
        "/v1/events",
        json={
            "title": "Imminent Programme",
            "type": "programme",
            "venueId": venue.json()["id"],
            "startTimeUtc": start,
            "endTimeUtc": start + 60 * MINUTE_MS,
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]
    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert assign.status_code in (200, 204), assign.text
    return event_id, start


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R48")
@pytest.mark.requirement("credit:R48c")
async def test_should_refund_when_occurrence_cancelled_after_marking(
    client: AsyncClient, db_session: AsyncSession
):
    """R48: a cancelled session costs nothing, like approved leave."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    event_id, start = await imminent_programme(client, admin_token, "alice")

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )
    assert marked.status_code == 200, marked.text
    assert await balance_of(client, admin_token, account["accountId"]) == 9

    cancelled = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/cancel",
        json={
            "version": await occurrence_version(client, admin_token, event_id, start),
            "reason": "Ice plant failure",
        },
        headers=auth(admin_token),
    )

    assert cancelled.status_code == 204, cancelled.text
    assert await balance_of(client, admin_token, account["accountId"]) == 10


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R81")
async def test_should_record_refund_on_statement_when_occurrence_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    """R81: the reversal is visible to the member, not a silent adjustment."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    _ = await open_account(client, admin_token, "alice", credits=10)
    event_id, start = await imminent_programme(client, admin_token, "alice")
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/cancel",
        json={
            "version": await occurrence_version(client, admin_token, event_id, start),
            "reason": "Ice plant failure",
        },
        headers=auth(admin_token),
    )

    statement = await client.get(
        "/v1/mycredits/by_id/alice/entries", headers=auth(member_token)
    )
    refunds = [
        row for row in statement.json()["items"] if row["entryType"] == "sessionRefund"
    ]
    assert len(refunds) == 1
    assert refunds[0]["amount"] == 1
    assert refunds[0]["occurrenceTimeUtc"] == start


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R48")
async def test_should_not_charge_when_marking_a_member_on_a_cancelled_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """R48: a cancelled occurrence is uncharged, so nothing can be spent on it."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    event_id, start = await imminent_programme(client, admin_token, "alice")
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/cancel",
        json={
            "version": await occurrence_version(client, admin_token, event_id, start),
            "reason": "Ice plant failure",
        },
        headers=auth(admin_token),
    )

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert marked.status_code == 422
    assert await balance_of(client, admin_token, account["accountId"]) == 10
