"""Resolution rules of notifications_requirements.md (#493).

R37: the resolution flows that delete an actionable notice from every feed,
beyond the ones #102's tests cover. R67: settling a withdrawal request
without approving it notifies nobody.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_member_user,
    create_registered_user,
)
from .redesign_helpers import (
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    enrollment_of,
    notifications_for,
)


async def _feed_types(client: AsyncClient, token: str) -> list[str]:
    response = await client.get("/v1/notifications", headers=auth(token))
    assert response.status_code == 200, response.text
    return [item["type"] for item in response.json()["items"]]


async def _programme(client: AsyncClient, admin: str) -> tuple[int, int]:
    """A daily programme starting in two days; returns (event id, first slot)."""
    start = at(days=2)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, start=start)
    return programme["id"], start


async def _all_ids(db_session: AsyncSession) -> set[int]:
    db_session.expire_all()
    result = await db_session.execute(select(Notification.id))
    return set(result.scalars().all())


@pytest.mark.requirement("notifications:R37")
@pytest.mark.asyncio
async def test_should_delete_invitation_notice_when_member_declines(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    amy = await create_member_user(db_session, "amy")
    event_id, _ = await _programme(client, admin)
    invite = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )
    assert invite.status_code == 204, invite.text
    assert await _feed_types(client, amy) == ["enrollment.opened"]

    decline = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/decline", headers=auth(amy)
    )

    assert decline.status_code == 204, decline.text
    assert await _feed_types(client, amy) == []


@pytest.mark.requirement("notifications:R37")
@pytest.mark.asyncio
async def test_should_delete_request_notice_when_admin_rejects_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    amy = await create_member_user(db_session, "amy")
    event_id, _ = await _programme(client, admin)
    request = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/request", headers=auth(amy)
    )
    assert request.status_code == 204, request.text
    assert "enrollment.rsvp" in await _feed_types(client, admin)

    reject = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject",
        json={"membernames": ["amy"], "reason": "Full"},
        headers=auth(admin),
    )

    assert reject.status_code == 204, reject.text
    assert "enrollment.rsvp" not in await _feed_types(client, admin)


async def _leave_requested(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int]:
    """Admin, amy's token, event id and slot, with amy's leave requested."""
    admin = await create_admin_user(db_session)
    amy = await create_member_user(db_session, "amy")
    event_id, slot = await _programme(client, admin)
    assigned = await assign(client, admin, event_id, "amy")
    assert assigned.status_code == 204, assigned.text
    leave = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "doctor"},
        headers=auth(amy),
    )
    assert leave.status_code == 204, leave.text
    assert "attendance.correction_requested" in await _feed_types(client, admin)
    return admin, amy, event_id, slot


@pytest.mark.requirement("notifications:R37")
@pytest.mark.asyncio
async def test_should_delete_leave_notice_when_admin_rejects_leave(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, slot = await _leave_requested(client, db_session)

    reject = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/reject",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    assert reject.status_code == 204, reject.text
    assert "attendance.correction_requested" not in await _feed_types(client, admin)


@pytest.mark.requirement("notifications:R37")
@pytest.mark.asyncio
async def test_should_delete_leave_notice_when_member_cancels_leave(
    client: AsyncClient, db_session: AsyncSession
):
    admin, amy, event_id, slot = await _leave_requested(client, db_session)

    cancel = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/occurrences/{slot}/leave/cancel",
        headers=auth(amy),
    )

    assert cancel.status_code == 204, cancel.text
    assert "attendance.correction_requested" not in await _feed_types(client, admin)


@pytest.mark.requirement("notifications:R37")
@pytest.mark.asyncio
async def test_should_delete_approval_notice_when_admin_blocks_pending_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    rosa = await create_registered_user(db_session, "rosa")
    _ = await attach_identity_document(db_session, "rosa")
    submit = await client.post("/v1/users/me/submit-for-review", headers=auth(rosa))
    assert submit.status_code == 200, submit.text
    assert await _feed_types(client, admin) == ["user.registration_pending"]

    block = await client.post("/v1/users/by_id/rosa/block", headers=auth(admin))

    assert block.status_code == 200, block.text
    assert await _feed_types(client, admin) == []


async def _withdrawal_requested(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int]:
    """Admin, amy's token and event id, with amy's withdrawal requested."""
    admin = await create_admin_user(db_session)
    amy = await create_member_user(db_session, "amy")
    event_id, _ = await _programme(client, admin)
    assigned = await assign(client, admin, event_id, "amy")
    assert assigned.status_code == 204, assigned.text
    withdraw = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/withdraw",
        json={"reason": "moving"},
        headers=auth(amy),
    )
    assert withdraw.status_code == 204, withdraw.text
    assert (
        len(await notifications_for(db_session, "admin", "enrollment.cancelled_self"))
        == 1
    )
    return admin, amy, event_id


@pytest.mark.requirement("notifications:R67")
@pytest.mark.asyncio
async def test_should_notify_nobody_when_admin_rejects_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id = await _withdrawal_requested(client, db_session)
    before = await _all_ids(db_session)

    reject = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    assert reject.status_code == 204, reject.text
    assert await enrollment_of(client, admin, event_id, "amy") == "assigned"
    assert await _all_ids(db_session) <= before


@pytest.mark.requirement("notifications:R67")
@pytest.mark.asyncio
async def test_should_notify_nobody_when_member_cancels_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    admin, amy, event_id = await _withdrawal_requested(client, db_session)
    before = await _all_ids(db_session)

    cancel = await client.post(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments/cancel-withdraw",
        headers=auth(amy),
    )

    assert cancel.status_code == 204, cancel.text
    assert await enrollment_of(client, admin, event_id, "amy") == "assigned"
    assert await _all_ids(db_session) <= before
