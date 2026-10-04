"""No rejoin while a deferred departure settlement is pending (#469).

A member who leaves while an occurrence they are covered for is under way
has their credit settled at its end (programme R29c-R29e). Re-adding them
before then left the pending disposition on the reused row, and the sweep
later penalised them and moved their bound credit out while they were
active again. Every rejoin path is refused with 409 ``INVALID_STATE`` until
the settlement has run.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services.credit_sweep import sweep_credit_settlements

from .credit_helpers import balance_of, open_account
from .enrollment_rejoin_helpers import (
    accept,
    admin_record,
    approve,
    assign_trial,
    invite,
    member_record,
    remove,
    request_join,
)
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    backdate_enrollment,
    create_programme,
    create_venue,
)


async def _pending_departure(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int, str]:
    """A member removed mid-occurrence, their settlement deferred to its end.

    Returns ``(admin token, member token, programme id, slot under way,
    bound account id)``.
    """
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    slot = at(minutes=-30)
    programme = await create_programme(
        client, admin, venue, start=slot, end=slot + 2 * HOUR_MS
    )
    event_id = programme["id"]
    account = await open_account(client, admin, "skater", credits=10, event_id=event_id)
    assert (await assign(client, admin, event_id, "skater")).status_code == 204
    await backdate_enrollment(db_session, event_id, "skater", slot - HOUR_MS)
    removed = await remove(
        client,
        admin,
        event_id,
        "skater",
        creditDisposition={
            "penalty": 2,
            "validFromUtc": at(days=-1),
            "validUntilUtc": at(days=90),
            "reason": "Leaving",
        },
    )
    assert removed.status_code == 204, removed.text
    await db_session.commit()
    return admin, member, event_id, slot, str(account["accountId"])


async def _assert_still_removed(
    client: AsyncClient, admin: str, member: str, event_id: int
) -> None:
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "removed"
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "removed"


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_refuse_assign_when_departure_settlement_is_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, bound = await _pending_departure(client, db_session)

    response = await assign(client, admin, event_id, "skater")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    await _assert_still_removed(client, admin, member, event_id)
    assert await balance_of(client, admin, bound) == 10


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_refuse_assign_trial_when_departure_settlement_is_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, _ = await _pending_departure(client, db_session)
    _ = await open_account(
        client, admin, "skater", credits=2, event_id=event_id, is_trial=True
    )
    await db_session.commit()

    response = await assign_trial(client, admin, event_id, "skater")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    await _assert_still_removed(client, admin, member, event_id)


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_refuse_invite_accept_when_departure_settlement_is_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, _ = await _pending_departure(client, db_session)
    assert (await invite(client, admin, event_id, "skater")).status_code == 204
    await db_session.commit()

    response = await accept(client, member, event_id, "skater")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "invited"
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "invited"


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_refuse_request_approval_when_departure_settlement_is_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, _ = await _pending_departure(client, db_session)
    requested = await request_join(client, member, event_id, "skater")
    assert requested.status_code == 204, requested.text
    await db_session.commit()

    response = await approve(client, admin, event_id, "skater")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "requested"
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "requested"


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_assign_when_departure_settlement_has_run(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, slot, bound = await _pending_departure(client, db_session)
    await sweep_credit_settlements(db_session, slot + 2 * HOUR_MS + MINUTE_MS)
    await db_session.commit()
    assert await balance_of(client, admin, bound) == 0

    response = await assign(client, admin, event_id, "skater")

    assert response.status_code == 204, response.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "assigned"
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "assigned"
