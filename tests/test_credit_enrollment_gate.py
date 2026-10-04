"""The credit check on enrollment and departure (#500).

Covers credit R36, R37, R38, R38a, the refused-enrollment half of R85, the
rejection half of R73, R95 and R98a from
``docs/credit_system_requirements.md``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.credit_entry import CreditEntry
from club_server.db.models.credit_session_charge import CreditSessionCharge

from .credit_helpers import days_from_now, get_account, open_account
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    audit_rows,
    auth,
    create_camp,
    create_programme,
    create_venue,
    enrollment_of,
    mark,
    notifications_for,
)


async def _programme(client: AsyncClient, admin: str, start: int) -> int:
    """A daily programme whose first occurrence is ``start``."""
    venue = await create_venue(client, admin)
    return (await create_programme(client, admin, venue, start=start))["id"]


async def _roster_row(client: AsyncClient, token: str, event_id: int) -> dict:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/credits", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    (row,) = response.json()["items"]
    return row


# --- R36, R37: one usable credit, judged now ---


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R36")
async def test_should_assign_member_holding_one_credit_to_a_daily_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    event_id = await _programme(client, admin, at(days=1))
    account = await open_account(client, admin, "alice", credits=1, event_id=event_id)

    response = await assign(client, admin, event_id, "alice")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, event_id, "alice") == "assigned"
    assert (await get_account(client, admin, str(account["accountId"])))["balance"] == 1


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R37")
async def test_should_assign_member_when_credit_expires_before_programme_starts(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    event_id = await _programme(client, admin, at(days=10))
    expires = days_from_now(3)
    _ = await open_account(client, admin, "alice", credits=4, valid_until=expires)

    response = await assign(client, admin, event_id, "alice")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, event_id, "alice") == "assigned"
    row = await _roster_row(client, admin, event_id)
    assert (row["usableCredits"], row["nextExpiryUtc"]) == (4, expires)


# --- R38, R38a, R85: a refused enrollment leaves nothing behind ---


async def _refused_assign(client: AsyncClient, db_session: AsyncSession):
    """Assign alice, who holds no credit, to a programme; return (admin, event, response)."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()
    event_id = await _programme(client, admin, at(days=1))
    response = await assign(client, admin, event_id, "alice")
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CREDIT"
    return admin, event_id, response


@pytest.mark.requirement("notifications:R8")
@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R38")
async def test_should_create_no_enrollment_or_notice_when_assign_refused_for_credit(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, _ = await _refused_assign(client, db_session)

    assert await enrollment_of(client, admin, event_id, "alice") is None
    assert (
        await notifications_for(db_session, "alice", "enrollment.admin_enrolled") == []
    )


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R38a")
async def test_should_name_member_and_amount_when_enrollment_refused_for_credit(
    client: AsyncClient, db_session: AsyncSession
):
    _, _, response = await _refused_assign(client, db_session)

    message = response.json()["detail"]["message"]
    assert "'alice'" in message
    assert "1 required" in message
    assert "contact" not in message.lower()


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R85")
async def test_should_write_no_entry_or_audit_row_when_enrollment_refused(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, _ = await _refused_assign(client, db_session)

    ledger = await client.get(
        "/v1/credits/entries", params={"membername": "alice"}, headers=auth(admin)
    )
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["items"] == []
    assert await audit_rows(db_session, "enrollment_assigned") == []


# --- R73: rejecting a withdrawal leaves every account untouched ---


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R73")
async def test_should_leave_accounts_untouched_when_withdrawal_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "alice")
    event_id = await _programme(client, admin, at(days=1))
    bound = await open_account(client, admin, "alice", credits=20, event_id=event_id)
    assert (await assign(client, admin, event_id, "alice")).status_code == 204
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member),
    )
    assert requested.status_code in (200, 204), requested.text

    rejected = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["alice"]},
        headers=auth(admin),
    )

    assert rejected.status_code == 204, rejected.text
    assert await enrollment_of(client, admin, event_id, "alice") == "assigned"
    accounts = await client.get("/v1/mycredits/by_id/alice", headers=auth(member))
    assert accounts.status_code == 200, accounts.text
    assert [
        (a["accountId"], a["kind"], a["balance"], a["state"]) for a in accounts.json()
    ] == [(bound["accountId"], "event", 20, "usable")]


# --- R95: a deployment without credit gates and charges nothing ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R95")
async def test_should_enrol_and_mark_without_credit_when_system_disabled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    start = at(minutes=10)
    event_id = await _programme(client, admin, start)

    assigned = await assign(client, admin, event_id, "alice")
    assert assigned.status_code == 204, assigned.text
    assert await enrollment_of(client, admin, event_id, "alice") == "assigned"
    marked = await mark(client, admin, event_id, start, "alice")

    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []
    assert [r["membername"] for r in marked.json()["marked"]] == ["alice"]
    entries = await db_session.execute(select(func.count()).select_from(CreditEntry))
    charges = await db_session.execute(
        select(func.count()).select_from(CreditSessionCharge)
    )
    assert (entries.scalar_one(), charges.scalar_one()) == (0, 0)
    assert await audit_rows(db_session, "credit_deducted") == []


# --- R98a: no disposition on a camp or one-off departure ---


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R98a")
async def test_should_reject_credit_disposition_when_member_leaves_a_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, count=3)
    assert (await assign(client, admin, camp["id"], "alice")).status_code == 204

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/enrollments/remove",
        json={
            "membernames": ["alice"],
            "creditDisposition": {
                "penalty": 0,
                "validFromUtc": days_from_now(-1),
                "validUntilUtc": days_from_now(90),
                "reason": "Left the camp",
            },
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CREDIT_DISPOSITION_NOT_APPLICABLE"
    assert await enrollment_of(client, admin, camp["id"], "alice") == "assigned"
