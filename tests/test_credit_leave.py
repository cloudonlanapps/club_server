"""Credit across attendance transitions: leave, repeats and refusals (#500).

Covers credit R41a, R44b, R45, R47, R48b and the refused-attendance half of
R85 from ``docs/credit_system_requirements.md``.

Leave is declared at least two hours before an occurrence and its register
opens thirty minutes before, so a leave request on a marked record is made
here by the super-admin, the one caller the leave window does not bind.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import drain_account, entries_for, get_account, open_account
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    audit_rows,
    auth,
    cancel_occurrence,
    create_camp,
    create_programme,
    create_venue,
    mark,
)

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def _programme_with_alice(
    client: AsyncClient, db_session: AsyncSession, *, minutes: int = 10
) -> tuple[str, str, int, int, str]:
    """Alice holding ten general credits, assigned to a daily programme.

    Returns (admin token, alice's token, event id, first occurrence, account code).
    """
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    start = at(minutes=minutes)
    event = await create_programme(client, admin, venue, start=start)
    account = await open_account(client, admin, "alice", credits=10)
    assert (await assign(client, admin, event["id"], "alice")).status_code == 204
    return admin, alice, event["id"], start, str(account["accountId"])


async def _register(client: AsyncClient, token: str, event_id: int, slot: int):
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/attendance",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return {row["membername"]: row["status"] for row in response.json()}


async def _balance(client: AsyncClient, admin: str, code: str) -> int:
    return int((await get_account(client, admin, code))["balance"])


async def _request_leave(client: AsyncClient, token: str, event_id: int, slot: int):
    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(token),
    )
    assert response.status_code == 204, response.text


async def _decide_leave(
    client: AsyncClient, admin: str, event_id: int, slot: int, decision: str
):
    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/{decision}",
        json={"membernames": ["alice"]},
        headers=auth(admin),
    )
    assert response.status_code in (200, 204), response.text


async def _deductions(client: AsyncClient, admin: str) -> list[dict]:
    return await entries_for(
        client, admin, membername="alice", entryType="sessionDeduction"
    )


# --- R44b: a repeated write cannot charge twice ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R44b")
async def test_should_charge_once_when_the_same_mark_is_written_twice(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, start, code = await _programme_with_alice(client, db_session)

    first = await mark(client, admin, event_id, start, "alice")
    again = await mark(client, admin, event_id, start, "alice")

    assert (first.status_code, again.status_code) == (200, 200)
    assert await _balance(client, admin, code) == 9
    assert len(await _deductions(client, admin)) == 1


# --- R45: approved leave costs nothing ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R45")
async def test_should_charge_nothing_when_leave_is_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin, alice, event_id, start, code = await _programme_with_alice(
        client, db_session, minutes=240
    )
    await _request_leave(client, alice, event_id, start)

    await _decide_leave(client, admin, event_id, start, "approve")

    assert await _register(client, admin, event_id, start) == {"alice": "onLeave"}
    assert await _balance(client, admin, code) == 10
    assert await _deductions(client, admin) == []


# --- R47: each leave transition reconciles the charge ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R47")
async def test_should_refund_when_charged_record_moves_to_approved_leave(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, start, code = await _programme_with_alice(client, db_session)
    assert (await mark(client, admin, event_id, start, "alice")).status_code == 200
    await _request_leave(client, admin, event_id, start)
    assert await _balance(client, admin, code) == 9

    await _decide_leave(client, admin, event_id, start, "approve")

    assert await _register(client, admin, event_id, start) == {"alice": "onLeave"}
    assert await _balance(client, admin, code) == 10
    refunds = await entries_for(
        client, admin, membername="alice", entryType="sessionRefund"
    )
    assert [(r["amount"], r["occurrenceTimeUtc"]) for r in refunds] == [(1, start)]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R47")
async def test_should_charge_when_present_is_marked_over_approved_leave(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, start, code = await _programme_with_alice(client, db_session)
    await _request_leave(client, admin, event_id, start)
    await _decide_leave(client, admin, event_id, start, "approve")
    assert await _balance(client, admin, code) == 10

    marked = await mark(client, admin, event_id, start, "alice")

    assert marked.status_code == 200, marked.text
    assert await _register(client, admin, event_id, start) == {"alice": "present"}
    assert await _balance(client, admin, code) == 9


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R47")
async def test_should_keep_the_one_charge_when_leave_over_a_mark_is_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, start, code = await _programme_with_alice(client, db_session)
    assert (await mark(client, admin, event_id, start, "alice")).status_code == 200
    await _request_leave(client, admin, event_id, start)

    await _decide_leave(client, admin, event_id, start, "reject")

    assert await _register(client, admin, event_id, start) == {"alice": "present"}
    assert await _balance(client, admin, code) == 9
    assert len(await _deductions(client, admin)) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R47")
async def test_should_charge_nothing_when_leave_without_a_mark_is_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin, alice, event_id, start, code = await _programme_with_alice(
        client, db_session, minutes=240
    )
    await _request_leave(client, alice, event_id, start)

    await _decide_leave(client, admin, event_id, start, "reject")

    assert await _register(client, admin, event_id, start) == {}
    assert await _balance(client, admin, code) == 10
    assert await _deductions(client, admin) == []


# --- R41a, R85: a refused mark leaves nothing behind ---


async def _refused_mark(client: AsyncClient, db_session: AsyncSession):
    """Alice, enrolled with credit that has since been drained, marked present."""
    admin, _, event_id, start, code = await _programme_with_alice(client, db_session)
    await drain_account(client, admin, code)
    refused = await mark(client, admin, event_id, start, "alice")
    assert refused.status_code == 200, refused.text
    assert [r["membername"] for r in refused.json()["refused"]] == ["alice"]
    return admin, event_id, start


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R41a")
async def test_should_leave_no_attendance_record_when_mark_refused_for_credit(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, start = await _refused_mark(client, db_session)

    assert await _register(client, admin, event_id, start) == {}
    assert await _deductions(client, admin) == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R85")
async def test_should_write_no_charge_or_audit_row_when_mark_refused(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, _ = await _refused_mark(client, db_session)

    assert await _deductions(client, admin) == []
    assert await audit_rows(db_session, "credit_deducted") == []
    assert await audit_rows(db_session, "attendance_marked") == []


# --- R48b: a camp register never reaches credit ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R48b")
async def test_should_mark_and_cancel_camp_register_when_member_holds_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    start = at(minutes=10)
    camp = await create_camp(client, admin, venue, start=start, count=3)
    assert (await assign(client, admin, camp["id"], "alice")).status_code == 204

    marked = await mark(client, admin, camp["id"], start, "alice")
    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []
    assert await _register(client, admin, camp["id"], start) == {"alice": "present"}
    cancelled = await cancel_occurrence(client, admin, camp["id"], start)

    assert cancelled.status_code == 204, cancelled.text
    assert await entries_for(client, admin, membername="alice") == []
    assert await audit_rows(db_session, "credit_deducted") == []
    assert await audit_rows(db_session, "credit_refunded") == []
