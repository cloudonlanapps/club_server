"""Credit and attendance, coupled (#294).

Covers spending (R41-R49) and the reconciliation rule (R44a, R44b) from
``docs/credit_system_requirements.md``. Charging is expressed as a
reconciliation rather than per-transition actions, so these tests drive
the transitions and assert the balance that results.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    balance_of,
    create_member,
    drain_account,
    open_account,
)
from .helpers import create_admin_user
from .test_attendance import create_venue, future_time_ms, move_event_to_recent_past

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def programme_with_member(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, username: str
) -> tuple[int, int]:
    """Create a past programme occurrence with ``username`` assigned to it."""
    venue_id = await create_venue(client, admin_token)
    start = future_time_ms(24)
    response = await client.post(
        "/v1/events",
        json={
            "title": "Credit Programme",
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
    past_start = await move_event_to_recent_past(db_session, event_id, [username])
    return event_id, past_start


async def mark(
    client: AsyncClient,
    admin_token: str,
    event_id: int,
    occurrence: int,
    username: str,
    status_value: str,
):
    """Mark one member's attendance and return the response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": username, "status": status_value}]},
        headers=auth(admin_token),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R33")
@pytest.mark.requirement("credit:R44")
async def test_should_deduct_one_credit_when_marked_present(
    client: AsyncClient, db_session: AsyncSession
):
    """R44: a chargeable status costs the session's price."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )

    response = await mark(client, admin_token, event_id, occurrence, "alice", "present")

    assert response.status_code == 200, response.text
    assert await balance_of(client, admin_token, account["accountId"]) == 9


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R46")
async def test_should_not_deduct_twice_when_status_changes_within_chargeable_set(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: present -> absent costs nothing further."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )

    _ = await mark(client, admin_token, event_id, occurrence, "alice", "present")
    _ = await mark(client, admin_token, event_id, occurrence, "alice", "absent")
    _ = await mark(client, admin_token, event_id, occurrence, "alice", "late")

    assert await balance_of(client, admin_token, account["accountId"]) == 9


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R21")
@pytest.mark.requirement("credit:R49")
@pytest.mark.requirement("credit:R54")
@pytest.mark.requirement("credit:R47")
async def test_should_refund_when_attendance_cleared(
    client: AsyncClient, db_session: AsyncSession
):
    """R47/R49: clearing the record returns the credit to its source."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=10)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await mark(client, admin_token, event_id, occurrence, "alice", "present")
    assert await balance_of(client, admin_token, account["accountId"]) == 9

    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance/alice",
        headers=auth(admin_token),
    )

    assert cleared.status_code == 204, cleared.text
    assert await balance_of(client, admin_token, account["accountId"]) == 10


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R25")
@pytest.mark.requirement("credit:R26")
async def test_should_prefer_event_bound_account_over_general(
    client: AsyncClient, db_session: AsyncSession
):
    """R25/R26: a usable event-bound account is never bypassed."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    general = await open_account(client, admin_token, "alice", credits=10)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    bound = await open_account(
        client, admin_token, "alice", credits=5, event_id=event_id
    )

    _ = await mark(client, admin_token, event_id, occurrence, "alice", "present")

    assert await balance_of(client, admin_token, bound["accountId"]) == 4
    assert await balance_of(client, admin_token, general["accountId"]) == 10


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R41")
@pytest.mark.requirement("credit:R41b1")
async def test_should_refuse_mark_when_member_has_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R41: a member who cannot pay cannot be marked."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=1)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await mark(client, admin_token, event_id, occurrence, "alice", "present")
    assert await balance_of(client, admin_token, account["accountId"]) == 0
    # The next session must be a real occurrence (#470): move the one-slot
    # programme to a new slot rather than marking a time one millisecond on.
    next_session = await move_event_to_recent_past(
        db_session, event_id, ["alice"], hours_ago=2
    )

    second = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{next_session}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert second.status_code == 200
    assert second.json()["marked"] == []
    assert len(second.json()["refused"]) == 1
    assert second.json()["refused"][0]["code"] == "INSUFFICIENT_CREDIT"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R41b")
@pytest.mark.requirement("credit:R41c")
async def test_should_mark_others_when_one_member_has_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R41b: one lapsed package must not fail a whole register."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    await create_member(db_session, "bob")
    _ = await open_account(client, admin_token, "bob", credits=10)
    alice_account = await open_account(client, admin_token, "alice", credits=1)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["bob"]},
        headers=auth(admin_token),
    )
    await drain_account(client, admin_token, alice_account["accountId"])
    # Moving the event again moves its slot; mark the slot it now has (#470).
    occurrence = await move_event_to_recent_past(db_session, event_id, ["alice", "bob"])

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={
            "records": [
                {"membername": "alice", "status": "present"},
                {"membername": "bob", "status": "present"},
            ]
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["membername"] for row in body["marked"]] == ["bob"]
    assert [row["membername"] for row in body["refused"]] == ["alice"]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R41")
@pytest.mark.requirement("credit:R42")
@pytest.mark.requirement("credit:R43")
async def test_should_restore_member_when_new_account_opened(
    client: AsyncClient, db_session: AsyncSession
):
    """R42/R43: being blocked is the absence of credit, not a stored state."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=1)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    # Alice joined with credit and has since run out — the real-world route
    # into this state, since joining without credit is refused (R35).
    await drain_account(client, admin_token, account["accountId"])
    refused = await mark(client, admin_token, event_id, occurrence, "alice", "present")
    assert refused.json()["refused"][0]["code"] == "INSUFFICIENT_CREDIT"

    _ = await open_account(client, admin_token, "alice", credits=3)
    retry = await mark(client, admin_token, event_id, occurrence, "alice", "present")

    assert retry.json()["refused"] == []
    assert [row["membername"] for row in retry.json()["marked"]] == ["alice"]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R15")
@pytest.mark.requirement("credit:R81")
async def test_should_record_statement_entry_when_session_charged(
    client: AsyncClient, db_session: AsyncSession
):
    """R87: the member's statement shows what the session cost."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    _ = await open_account(client, admin_token, "alice", credits=10)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await mark(client, admin_token, event_id, occurrence, "alice", "present")

    statement = await client.get(
        "/v1/mycredits/by_id/alice/entries", headers=auth(member_token)
    )

    assert statement.status_code == 200, statement.text
    deductions = [
        row
        for row in statement.json()["items"]
        if row["entryType"] == "sessionDeduction"
    ]
    assert len(deductions) == 1
    assert deductions[0]["amount"] == -1
    assert deductions[0]["occurrenceTimeUtc"] == occurrence
    assert deductions[0]["actorUsername"] == "admin"
