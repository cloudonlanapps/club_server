"""Audit trail and concurrency for credit movements (#294, R31, R82, R84).

Money-adjacent operations write audit rows alongside their ledger entries,
every movement names the person behind it, and two simultaneous spends
cannot take the same last credit twice.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_member, open_account
from .helpers import create_admin_user, create_coach_user, create_regular_admin_user
from .test_attendance import create_venue, future_time_ms, move_event_to_recent_past

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def programme_with_member(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    username: str,
    organizer: str | None = None,
) -> tuple[int, int]:
    """A past programme occurrence with ``username`` assigned."""
    venue_id = await create_venue(client, admin_token)
    start = future_time_ms(24)
    body: dict[str, object] = {
        "title": "Audited Programme",
        "type": "programme",
        "venueId": venue_id,
        "startTimeUtc": start,
        "endTimeUtc": start + 60 * 60 * 1000,
    }
    if organizer is not None:
        body["organizerName"] = organizer
    response = await client.post("/v1/events", json=body, headers=auth(admin_token))
    event_id = response.json()["id"]
    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert assign.status_code in (200, 204), assign.text
    occurrence = await move_event_to_recent_past(db_session, event_id, [username])
    return event_id, occurrence


async def audit_actions(
    client: AsyncClient, admin_token: str, username: str
) -> list[str]:
    """Audit actions recorded against ``username``."""
    response = await client.get(
        "/v1/audit_log", params={"username": username}, headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text
    return [row["action"] for row in response.json()["rows"]]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R84")
async def test_should_write_audit_row_when_session_charged(
    client: AsyncClient, db_session: AsyncSession
):
    """R84: credit movements are auditable, not only visible on the ledger."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    _ = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert "credit_deducted" in await audit_actions(client, admin_token, "alice")


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R84")
async def test_should_write_audit_row_when_session_refunded(
    client: AsyncClient, db_session: AsyncSession
):
    """R84: the reversal is auditable too — that is the half people query."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    _ = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    _ = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance/alice",
        headers=auth(admin_token),
    )

    assert "credit_refunded" in await audit_actions(client, admin_token, "alice")


@pytest.mark.asyncio
async def test_should_record_the_coach_who_marked_as_the_actor(
    client: AsyncClient, db_session: AsyncSession
):
    """R82: credit always has a person behind it, never a system placeholder."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "rinkcoach")
    member_token = await create_member(db_session, "alice")
    _ = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice", organizer="rinkcoach"
    )

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(coach_token),
    )
    assert marked.status_code == 200, marked.text

    statement = await client.get(
        "/v1/mycredits/by_id/alice/entries", headers=auth(member_token)
    )
    deduction = next(
        row
        for row in statement.json()["items"]
        if row["entryType"] == "sessionDeduction"
    )
    assert deduction["actorUsername"] == "rinkcoach"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R82")
@pytest.mark.requirement("credit:R79")
async def test_should_never_write_an_empty_actor_on_a_ledger_entry(
    client: AsyncClient, db_session: AsyncSession
):
    """R82: every movement a person caused names that person.

    Neither an empty string nor null: the column is a user reference, and
    credit always has someone behind it. Covers every entry type, and the
    deferred departure the sweep applies on the admin's behalf. The one
    exception, the release of a terminated programme (R73a), is the club's
    action with no member at fault, and is not exercised here.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_member(db_session, "alice")
    _ = await create_member(db_session, "bob")
    spend = await open_account(client, admin_token, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin_token, db_session, "alice"
    )
    for step in (
        client.post(
            f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
            json={"records": [{"membername": "alice", "status": "present"}]},
            headers=auth(admin_token),
        ),
        client.delete(
            f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance/alice",
            headers=auth(admin_token),
        ),
        client.post(
            f"/v1/credits/accounts/{spend['accountId']}/extend",
            json={"validUntilUtc": future_time_ms(24 * 120), "reason": "Goodwill"},
            headers=auth(admin_token),
        ),
        client.post(
            f"/v1/credits/accounts/{spend['accountId']}/reverse",
            json={"reason": "Granted in error", "credits": 1},
            headers=auth(admin_token),
        ),
        client.post(
            f"/v1/credits/accounts/{spend['accountId']}/transfer",
            json={
                "penalty": 1,
                "validFromUtc": future_time_ms(-24),
                "validUntilUtc": future_time_ms(24 * 90),
                "reason": "Moved",
            },
            headers=auth(admin_token),
        ),
    ):
        response = await step
        assert response.status_code in (200, 204), response.text
    await _deferred_departure(client, db_session, admin_token)

    ledger = await client.get(
        "/v1/credits/entries", params={"limit": 200}, headers=auth(admin_token)
    )

    assert ledger.status_code == 200, ledger.text
    rows = ledger.json()["items"]
    assert {row["membername"] for row in rows} == {"alice", "bob"}
    assert {row["entryType"] for row in rows} == {
        "grant",
        "sessionDeduction",
        "sessionRefund",
        "validityExtended",
        "grantReversal",
        "penalty",
        "transferOut",
        "transferIn",
    }
    for row in rows:
        assert isinstance(row["actorUsername"], str), row
        assert row["actorUsername"] != "", row


async def _deferred_departure(
    client: AsyncClient, db_session: AsyncSession, admin_token: str
) -> None:
    """Remove bob mid-session from a programme of his own, then run the sweep.

    Another admin organizes it, so it does not clash with the first
    programme's organizer.
    """
    from club_server.services.credit_sweep import sweep_credit_settlements

    from .redesign_helpers import (
        HOUR_MS,
        MINUTE_MS,
        assign,
        at,
        backdate_enrollment,
        create_programme,
    )

    organizer_token = await create_regular_admin_user(db_session, "organizer")
    venue = await create_venue(client, admin_token)
    slot = at(minutes=-30)
    programme = await create_programme(
        client, organizer_token, venue, start=slot, end=slot + 2 * HOUR_MS
    )
    _ = await open_account(
        client, admin_token, "bob", credits=4, event_id=programme["id"]
    )
    assigned = await assign(client, admin_token, programme["id"], "bob")
    assert assigned.status_code == 204, assigned.text
    await backdate_enrollment(db_session, programme["id"], "bob", slot - HOUR_MS)
    removed = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/remove",
        json={
            "membernames": ["bob"],
            "creditDisposition": {
                "penalty": 1,
                "validFromUtc": at(days=-1),
                "validUntilUtc": at(days=90),
                "reason": "Left mid-session",
            },
        },
        headers=auth(admin_token),
    )
    assert removed.status_code == 204, removed.text
    _ = await sweep_credit_settlements(db_session, slot + 2 * HOUR_MS + MINUTE_MS)
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R31")
async def test_should_lock_accounts_when_selecting_them_to_spend(
    db_session: AsyncSession,
):
    """R31: the rows a spend will decrement are locked while it decides.

    Without the lock two simultaneous attendance writes can each read the
    same balance and each spend the last credit, taking it negative — which
    R12 forbids and no unique constraint prevents, because they are two
    different occurrences. This asserts the lock is taken; the race itself
    is not reproducible in a single-session test harness.
    """
    from club_server.services.credit_selection import CreditSelection

    selection = CreditSelection(db_session)
    statement = selection.spending_query("alice", 1, is_trial=False)

    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE" in compiled.upper(), compiled
