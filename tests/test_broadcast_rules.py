"""Broadcast rules of notifications_requirements.md left untested by #401 (#493).

R115 (inactive and deleted names dropped), R116 (the event-staff audience),
R118 (malformed selectors), R126 (admin-only reads and revoke) and R128
(unknown broadcast ids).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
    create_user_with_status,
)
from .redesign_helpers import auth, create_programme, create_venue, notifications_for

PAYLOAD = {"v": 1, "type": "broadcast.message", "data": {"text": "Hello"}}


async def _send(client: AsyncClient, admin: str, selector: dict):
    return await client.post(
        "/v1/broadcasts",
        json={"audienceSelector": selector, "payload": PAYLOAD},
        headers=auth(admin),
    )


async def _broadcast_total(client: AsyncClient, admin: str) -> int:
    listing = await client.get("/v1/broadcasts", headers=auth(admin))
    assert listing.status_code == 200, listing.text
    return listing.json()["total"]


@pytest.mark.requirement("notifications:R115")
@pytest.mark.asyncio
async def test_should_drop_inactive_and_deleted_names_when_audience_is_a_list(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_user_with_status(db_session, "blake", UserStatus.blocked)
    _ = await create_user_with_status(
        db_session, "dora", UserStatus.active, deleted=True
    )

    response = await _send(
        client, admin, {"kind": "users", "usernames": ["amy", "blake", "dora"]}
    )

    assert response.status_code == 201, response.text
    assert response.json()["recipientCount"] == 1
    assert len(await notifications_for(db_session, "amy", "broadcast.message")) == 1
    for dropped in ("blake", "dora"):
        assert await notifications_for(db_session, dropped, "broadcast.message") == []


@pytest.mark.requirement("notifications:R116")
@pytest.mark.asyncio
async def test_should_reach_every_admin_and_coach_when_audience_is_event_staff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "ra")
    _ = await create_coach_user(db_session, "ca")
    _ = await create_member_user(db_session, "m1")
    venue = await create_venue(client, admin)
    event_id = (await create_programme(client, admin, venue))["id"]

    response = await _send(client, admin, {"kind": "event_staff", "eventId": event_id})

    assert response.status_code == 201, response.text
    assert response.json()["recipientCount"] == 3
    for staff in ("admin", "ra", "ca"):
        assert len(await notifications_for(db_session, staff, "broadcast.message")) == 1
    assert await notifications_for(db_session, "m1", "broadcast.message") == []


@pytest.mark.requirement("notifications:R118")
@pytest.mark.asyncio
async def test_should_reject_selector_when_group_is_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group = await client.post("/v1/groups", json={"name": "G"}, headers=auth(admin))
    gid = group.json()["id"]
    deleted = await client.delete(f"/v1/groups/by_id/{gid}", headers=auth(admin))
    assert deleted.status_code == 200, deleted.text
    await db_session.commit()

    response = await _send(client, admin, {"kind": "group", "groupId": gid})

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_AUDIENCE_SELECTOR"
    assert await _broadcast_total(client, admin) == 0


@pytest.mark.requirement("notifications:R118")
@pytest.mark.asyncio
async def test_should_reject_selector_when_required_field_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    selectors = [
        {"kind": "role"},
        {"kind": "group"},
        {"kind": "event_members"},
        {"kind": "event_staff"},
        {"kind": "users"},
    ]

    for selector in selectors:
        response = await _send(client, admin, selector)
        assert response.status_code == 422, selector
        assert response.json()["detail"]["code"] == "INVALID_AUDIENCE_SELECTOR"
    assert await _broadcast_total(client, admin) == 0


@pytest.mark.requirement("notifications:R126")
@pytest.mark.asyncio
async def test_should_refuse_broadcast_reads_and_revoke_when_caller_is_coach(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "ca")
    _ = await create_member_user(db_session, "amy")
    sent = await _send(client, admin, {"kind": "users", "usernames": ["amy"]})
    assert sent.status_code == 201, sent.text
    bid = sent.json()["id"]
    await db_session.commit()

    refused = [
        await client.get("/v1/broadcasts", headers=auth(coach)),
        await client.get(f"/v1/broadcasts/by_id/{bid}", headers=auth(coach)),
        await client.get(f"/v1/broadcasts/by_id/{bid}/recipients", headers=auth(coach)),
        await client.delete(f"/v1/broadcasts/by_id/{bid}", headers=auth(coach)),
    ]

    for response in refused:
        assert response.status_code == 403, response.text
    detail = await client.get(f"/v1/broadcasts/by_id/{bid}", headers=auth(admin))
    assert detail.json()["status"] == "sent"
    assert detail.json()["recipientCount"] == 1


@pytest.mark.requirement("notifications:R128")
@pytest.mark.asyncio
async def test_should_return_404_when_viewing_unknown_broadcast(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    responses = [
        await client.get("/v1/broadcasts/by_id/9999", headers=auth(admin)),
        await client.get("/v1/broadcasts/by_id/9999/recipients", headers=auth(admin)),
    ]

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "BROADCAST_NOT_FOUND"
