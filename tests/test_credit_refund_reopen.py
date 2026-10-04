"""A refund into a closed credit account reopens it (#479, R56).

A refund reaches the account that paid (R56) even after a departure has
emptied and closed it (R65–R70). Left closed, that credit could never be
spent, extended or transferred, so the refund reopens the account and
records why in the audit log.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    balance_of,
    create_member,
    days_from_now,
    get_account,
    open_account,
)
from .helpers import create_admin_user
from .redesign_helpers import audit_rows, occurrence_version

pytestmark = pytest.mark.usefixtures("credit_enabled")

MINUTE_MS = 60 * 1000
REOPEN = "reopen_credit_account"


async def _charged_then_departed(
    client: AsyncClient, admin_token: str, username: str
) -> tuple[int, int, str]:
    """A member charged for an imminent session, then removed with credit disposed.

    Returns (event id, occurrence time, code of the now-closed bound account).
    """
    venue = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=auth(admin_token)
    )
    start = days_from_now(0) + 10 * MINUTE_MS
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
    assert event.status_code == 201, event.text
    event_id = event.json()["id"]
    account = await open_account(
        client, admin_token, username, credits=10, event_id=event_id
    )
    code = str(account["accountId"])
    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert assign.status_code in (200, 204), assign.text
    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance",
        json={"records": [{"membername": username, "status": "present"}]},
        headers=auth(admin_token),
    )
    assert marked.status_code == 200, marked.text
    assert await balance_of(client, admin_token, code) == 9

    removed = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={
            "membernames": [username],
            "creditDisposition": {
                "penalty": 0,
                "validFromUtc": days_from_now(-1),
                "validUntilUtc": days_from_now(90),
                "reason": "Left the programme",
            },
        },
        headers=auth(admin_token),
    )
    assert removed.status_code in (200, 204), removed.text
    closed = await get_account(client, admin_token, code)
    assert closed["state"] == "closed"
    assert closed["balance"] == 0
    return event_id, start, code


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R56")
async def test_should_reopen_closed_account_when_cancellation_refunds_into_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id, start, code = await _charged_then_departed(client, admin_token, "alice")

    cancelled = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/cancel",
        json={
            "version": await occurrence_version(client, admin_token, event_id, start),
            "reason": "Ice plant failure",
        },
        headers=auth(admin_token),
    )

    assert cancelled.status_code == 204, cancelled.text
    account = await get_account(client, admin_token, code)
    assert account["state"] != "closed"
    assert account["balance"] == 1
    rows = await audit_rows(db_session, REOPEN)
    assert [(r.resource_type, r.resource_id) for r in rows] == [
        ("credit_account", code)
    ]
    assert rows[0].target_username == "alice"
    details = json.loads(rows[0].details)
    assert details["reason"] == "sessionRefund"
    assert details["eventId"] == event_id
    assert details["occurrenceTimeUtc"] == start

    extended = await client.post(
        f"/v1/credits/accounts/{code}/extend",
        json={"validUntilUtc": days_from_now(120), "reason": "Refunded session"},
        headers=auth(admin_token),
    )
    assert extended.status_code == 200, extended.text
    assert (await get_account(client, admin_token, code))["validUntilUtc"] == (
        extended.json()["validUntilUtc"]
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R56")
async def test_should_reopen_closed_account_when_cleared_attendance_refunds_into_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id, start, code = await _charged_then_departed(client, admin_token, "alice")

    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance/alice",
        headers=auth(admin_token),
    )

    assert cleared.status_code == 204, cleared.text
    account = await get_account(client, admin_token, code)
    assert account["state"] != "closed"
    assert account["balance"] == 1
    assert len(await audit_rows(db_session, REOPEN)) == 1

    transferred = await client.post(
        f"/v1/credits/accounts/{code}/transfer",
        json={
            "penalty": 0,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(90),
            "reason": "Move refunded session to general",
        },
        headers=auth(admin_token),
    )
    assert transferred.status_code == 200, transferred.text
    moved = await get_account(client, admin_token, code)
    assert moved["state"] == "closed"
    assert moved["balance"] == 0


@pytest.mark.asyncio
async def test_should_not_audit_a_reopen_when_refund_lands_in_open_account(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    code = str(account["accountId"])
    venue = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=auth(admin_token)
    )
    start = days_from_now(0) + 10 * MINUTE_MS
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
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )
    assert marked.status_code == 200, marked.text
    assert await balance_of(client, admin_token, code) == 9

    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/attendance/alice",
        headers=auth(admin_token),
    )

    assert cleared.status_code == 204, cleared.text
    assert await balance_of(client, admin_token, code) == 10
    assert (await get_account(client, admin_token, code))["state"] != "closed"
    assert await audit_rows(db_session, REOPEN) == []
