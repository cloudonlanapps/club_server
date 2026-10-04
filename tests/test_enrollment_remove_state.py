"""A member who has already left cannot be removed again (#467).

Removing a member who had already left rewrote ``withdrawn_at`` to the
moment of the removal, so they looked covered for every session since they
actually left, and their departure was settled a second time. Removal is
refused with 409 ``INVALID_STATE`` from a terminal status (withdrawn,
removed, declined, rejected); invited and requested rows stay removable.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .enrollment_rejoin_helpers import admin_record, invite, member_record, remove
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import assign, auth, create_programme, create_venue


async def _programme_with_member(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int]:
    """A future programme with ``skater`` assigned to it."""
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    return admin, member, programme["id"]


@pytest.mark.asyncio
async def test_should_refuse_removal_when_member_already_withdrew(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id = await _programme_with_member(client, db_session)
    withdraw = await client.post(
        f"/v1/myevents/by_id/skater/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member),
    )
    assert withdraw.status_code == 204, withdraw.text
    approved = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )
    assert approved.status_code == 204, approved.text
    before = await admin_record(client, admin, event_id, "skater")
    assert before["status"] == "withdrawn"
    await db_session.commit()

    response = await remove(client, admin, event_id, "skater", reason="Cleanup")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    after = await admin_record(client, admin, event_id, "skater")
    assert after["status"] == "withdrawn"
    assert after["withdrawnAtUtc"] == before["withdrawnAtUtc"]
    assert after["withdrawalReason"] == before["withdrawalReason"]
    mine = await member_record(client, member, event_id, "skater")
    assert mine["withdrawnAtUtc"] == before["withdrawnAtUtc"]


@pytest.mark.asyncio
async def test_should_refuse_removal_when_member_already_removed(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id = await _programme_with_member(client, db_session)
    assert (await remove(client, admin, event_id, "skater")).status_code == 204
    before = await admin_record(client, admin, event_id, "skater")
    await db_session.commit()

    response = await remove(client, admin, event_id, "skater", reason="Again")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    after = await admin_record(client, admin, event_id, "skater")
    assert after["status"] == "removed"
    assert after["withdrawnAtUtc"] == before["withdrawnAtUtc"]
    assert after["withdrawalReason"] is None
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "removed"


@pytest.mark.asyncio
async def test_should_remove_member_when_withdrawal_request_is_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, event_id = await _programme_with_member(client, db_session)
    withdraw = await client.post(
        f"/v1/myevents/by_id/skater/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member),
    )
    assert withdraw.status_code == 204, withdraw.text

    response = await remove(client, admin, event_id, "skater", reason="Leaving")

    assert response.status_code == 204, response.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "removed"
    assert record["withdrawnAtUtc"] is not None
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "removed"


@pytest.mark.asyncio
async def test_should_remove_member_when_still_invited(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    event_id = programme["id"]
    invited = await invite(client, admin, event_id, "skater")
    assert invited.status_code == 204, invited.text

    response = await remove(client, admin, event_id, "skater", reason="Changed plan")

    assert response.status_code == 204, response.text
    record = await admin_record(client, admin, event_id, "skater")
    assert record["status"] == "removed"
    mine = await member_record(client, member, event_id, "skater")
    assert mine["status"] == "removed"
