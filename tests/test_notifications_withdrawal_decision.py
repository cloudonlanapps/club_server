"""Deciding a withdrawal keeps staff's original request notice (#515).

notifications R37a: approving or rejecting a withdrawal deletes no other
notice of the enrollment; in particular staff's `enrollment.rsvp` request
notice stays in every staff feed.

The request notice survives to a withdrawal only when the request left its
requested state by a path that does not resolve it: here the admin removes
the request and then assigns the member directly.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .enrollment_rejoin_helpers import remove, request_join
from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    enrollment_of,
)


async def _request_notices(client: AsyncClient, token: str) -> list[dict]:
    response = await client.get("/v1/notifications", headers=auth(token))
    assert response.status_code == 200, response.text
    return [
        item
        for item in response.json()["items"]
        if item["type"] == "enrollment.rsvp"
        and item["payload"]["data"]["outcome"] == "requested"
    ]


async def _withdrawal_after_request(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int]:
    """Admin, coach, event id: amy requested, was removed, assigned, and
    has now asked to withdraw; both staff hold the request notice."""
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "carl")
    amy = await create_member_user(db_session, "amy")
    await db_session.commit()
    venue = await create_venue(client, admin)
    event_id = (await create_programme(client, admin, venue, start=at(days=2)))["id"]

    requested = await request_join(client, amy, event_id, "amy")
    assert requested.status_code == 204, requested.text
    removed = await remove(client, admin, event_id, "amy", reason="Full")
    assert removed.status_code == 204, removed.text
    assigned = await assign(client, admin, event_id, "amy")
    assert assigned.status_code == 204, assigned.text
    withdraw = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/withdraw",
        json={"reason": "moving"},
        headers=auth(amy),
    )
    assert withdraw.status_code == 204, withdraw.text
    assert await enrollment_of(client, admin, event_id, "amy") == "withdrawRequested"
    for token in (admin, coach):
        assert len(await _request_notices(client, token)) == 1
    return admin, coach, event_id


@pytest.mark.requirement("notifications:R37a")
@pytest.mark.asyncio
async def test_should_keep_request_notice_when_admin_approves_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    admin, coach, event_id = await _withdrawal_after_request(client, db_session)

    approved = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    assert approved.status_code == 204, approved.text
    assert await enrollment_of(client, admin, event_id, "amy") == "withdrawn"
    for token in (admin, coach):
        notices = await _request_notices(client, token)
        assert len(notices) == 1
        assert notices[0]["payload"]["data"]["memberUsername"] == "amy"


@pytest.mark.requirement("notifications:R37a")
@pytest.mark.asyncio
async def test_should_keep_request_notice_when_admin_rejects_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    admin, coach, event_id = await _withdrawal_after_request(client, db_session)

    rejected = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    assert rejected.status_code == 204, rejected.text
    assert await enrollment_of(client, admin, event_id, "amy") == "assigned"
    for token in (admin, coach):
        notices = await _request_notices(client, token)
        assert len(notices) == 1
        assert notices[0]["payload"]["data"]["memberUsername"] == "amy"
