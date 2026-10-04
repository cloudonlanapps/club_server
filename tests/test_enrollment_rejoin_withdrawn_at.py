"""Every rejoin path clears the old departure (#465).

A withdrawn or removed row is reused when the member comes back. Assign
always cleared ``withdrawn_at``; invite-then-accept and request-then-approve
kept it, so every occurrence after the old departure failed coverage and the
member could not be marked for sessions they had rejoined for.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .enrollment_rejoin_helpers import (
    accept,
    admin_record,
    approve,
    invite,
    member_record,
    remove,
    request_join,
    stamp_withdrawn_at,
)
from .helpers import (
    create_admin_user,
    create_member_user,
    create_regular_admin_user,
)
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


async def _departed_member(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int]:
    """A daily programme five days old and a member who left it on day one.

    Returns ``(admin token, member token, programme id, first slot)``.
    """
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    assert (await remove(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    await stamp_withdrawn_at(db_session, programme["id"], "skater", start + HOUR_MS)
    return admin, member, programme["id"], start


@pytest.mark.asyncio
async def test_should_clear_withdrawn_at_when_departed_member_accepts_new_invite(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _ = await _departed_member(client, db_session)
    assert (await invite(client, admin, event_id, "skater")).status_code == 204

    accepted = await accept(client, member, event_id, "skater")

    assert accepted.status_code == 204, accepted.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "accepted"
    assert record["withdrawnAtUtc"] is None
    assert record["withdrawalReason"] is None
    mine = await member_record(client, member, event_id, "skater")
    assert mine["withdrawnAtUtc"] is None


@pytest.mark.asyncio
async def test_should_clear_withdrawn_at_when_departed_members_request_is_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id, _ = await _departed_member(client, db_session)
    requested = await request_join(client, member, event_id, "skater")
    assert requested.status_code == 204, requested.text

    approved = await approve(client, admin, event_id, "skater")

    assert approved.status_code == 204, approved.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "accepted"
    assert record["withdrawnAtUtc"] is None
    mine = await member_record(client, member, event_id, "skater")
    assert mine["withdrawnAtUtc"] is None


@pytest.mark.asyncio
async def test_should_mark_rejoined_member_when_session_follows_old_departure(
    client: AsyncClient, db_session: AsyncSession
):
    """The business outcome: sessions after the old departure are covered.

    Marked by a regular admin: a super admin bypasses the coverage check.
    """
    admin, member, event_id, start = await _departed_member(client, db_session)
    marker = await create_regular_admin_user(db_session)
    assert (await invite(client, admin, event_id, "skater")).status_code == 204
    assert (await accept(client, member, event_id, "skater")).status_code == 204
    slot = start + 3 * DAY_MS
    await backdate_enrollment(db_session, event_id, "skater", slot - HOUR_MS)

    marked = await mark(client, marker, event_id, slot, "skater")

    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []


@pytest.mark.asyncio
async def test_should_keep_old_departure_while_rejoin_is_only_invited(
    client: AsyncClient, db_session: AsyncSession
):
    """An invitation is not a rejoin: the closed stint keeps its end."""
    admin, _, event_id, start = await _departed_member(client, db_session)

    invited = await invite(client, admin, event_id, "skater")

    assert invited.status_code == 204, invited.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "invited"
    assert record["withdrawnAtUtc"] == start + HOUR_MS
