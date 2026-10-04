"""A rejoin says whether the new stint is a trial (#468, credit R53).

A trial row is reused when the member comes back. The trial flag stayed set,
so a member whose trial ran out and who then bought a package was checked
against their (empty) trial credit: the rejoin failed with
``INSUFFICIENT_CREDIT``, or, with trial credit left, sessions kept drawing
from it instead of the package.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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
    DAY_MS,
    HOUR_MS,
    assign,
    at,
    backdate_enrollment,
    create_programme,
    create_venue,
    mark,
)


async def _trial_ended_then_package_bought(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int, str]:
    """A member whose one-credit trial ran out, holding a new paid package.

    Returns ``(admin token, member token, programme id, first slot, paid
    account id)``.
    """
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    event_id = programme["id"]
    _ = await open_account(
        client, admin, "skater", credits=1, event_id=event_id, is_trial=True
    )
    assert (await assign_trial(client, admin, event_id, "skater")).status_code == 204
    await backdate_enrollment(db_session, event_id, "skater", start - HOUR_MS)
    marked = await mark(client, admin, event_id, start, "skater")
    assert marked.status_code == 200, marked.text
    assert (await admin_record(client, admin, event_id, "skater"))[
        "status"
    ] == "removed"
    paid = await open_account(client, admin, "skater", credits=10, event_id=event_id)
    return admin, member, event_id, start, str(paid["accountId"])


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R53")
@pytest.mark.asyncio
async def test_should_assign_as_paid_member_when_trial_ended_and_package_bought(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, _ = await _trial_ended_then_package_bought(
        client, db_session
    )

    assigned = await assign(client, admin, event_id, "skater")

    assert assigned.status_code == 204, assigned.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "assigned"
    assert record["isTrial"] is False
    mine = await member_record(client, member, event_id, "skater")
    assert mine["isTrial"] is False


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R53")
@pytest.mark.asyncio
async def test_should_charge_paid_package_when_former_trial_member_is_marked(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, start, paid = await _trial_ended_then_package_bought(
        client, db_session
    )
    assert (await assign(client, admin, event_id, "skater")).status_code == 204
    slot = start + 2 * DAY_MS
    await backdate_enrollment(db_session, event_id, "skater", slot - HOUR_MS)

    marked = await mark(client, admin, event_id, slot, "skater")

    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []
    assert await balance_of(client, admin, paid) == 9
    assert (await admin_record(client, admin, event_id, "skater"))[
        "status"
    ] == "assigned"


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R53")
@pytest.mark.asyncio
async def test_should_accept_invite_as_paid_member_when_trial_ended(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, _ = await _trial_ended_then_package_bought(
        client, db_session
    )
    assert (await invite(client, admin, event_id, "skater")).status_code == 204

    accepted = await accept(client, member, event_id, "skater")

    assert accepted.status_code == 204, accepted.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "accepted"
    assert record["isTrial"] is False
    mine = await member_record(client, member, event_id, "skater")
    assert mine["isTrial"] is False


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R53")
@pytest.mark.asyncio
async def test_should_approve_request_as_paid_member_when_trial_ended(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _, _ = await _trial_ended_then_package_bought(
        client, db_session
    )
    requested = await request_join(client, member, event_id, "skater")
    assert requested.status_code == 204, requested.text

    approved = await approve(client, admin, event_id, "skater")

    assert approved.status_code == 204, approved.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "accepted"
    assert record["isTrial"] is False
    mine = await member_record(client, member, event_id, "skater")
    assert mine["isTrial"] is False


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_set_trial_flag_when_former_paid_member_is_assigned_a_trial(
    client: AsyncClient, db_session: AsyncSession
):
    """The flag follows the path taken, in both directions."""
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    event_id = programme["id"]
    _ = await open_account(client, admin, "skater", credits=5, event_id=event_id)
    assert (await assign(client, admin, event_id, "skater")).status_code == 204
    removed = await remove(
        client,
        admin,
        event_id,
        "skater",
        creditDisposition={
            "penalty": 0,
            "validFromUtc": at(days=-1),
            "validUntilUtc": at(days=90),
            "reason": "Leaving",
        },
    )
    assert removed.status_code == 204, removed.text
    _ = await open_account(
        client, admin, "skater", credits=1, event_id=event_id, is_trial=True
    )

    trial = await assign_trial(client, admin, event_id, "skater")

    assert trial.status_code == 204, trial.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "assignedTrial"
    assert record["isTrial"] is True
    mine = await member_record(client, member, event_id, "skater")
    assert mine["isTrial"] is True
