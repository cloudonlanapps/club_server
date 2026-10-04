"""Generated-notification rules of notifications_requirements.md (#493).

R56 (auto groups have no recipients), R76 (terminal enrollments are not
notified), R77 (programme termination) and R105 (forgotten-password reset).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import User
from club_server.mailer.sender import ConsoleEmailSender

from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    enrollment_of,
    get_event,
    notifications_for,
    terminate,
)


async def _auto_group(client: AsyncClient, admin: str) -> int:
    """An auto group whose criteria amy (female) matches."""
    created = await client.post(
        "/v1/groups", json={"name": "Girls", "gender": "female"}, headers=auth(admin)
    )
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "auto"
    gid = created.json()["id"]
    detail = await client.get(f"/v1/groups/by_id/{gid}", headers=auth(admin))
    assert [m["membername"] for m in detail.json()["members"]] == ["amy"]
    return gid


@pytest.mark.requirement("notifications:R56")
@pytest.mark.asyncio
async def test_should_notify_nobody_when_auto_group_is_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy", gender="female")
    gid = await _auto_group(client, admin)

    deleted = await client.delete(f"/v1/groups/by_id/{gid}", headers=auth(admin))

    assert deleted.status_code == 200, deleted.text
    fetched = await client.get(f"/v1/groups/by_id/{gid}", headers=auth(admin))
    assert fetched.json()["deletedAtUtc"] is not None
    assert await notifications_for(db_session, "amy", "group.archived") == []


@pytest.mark.requirement("notifications:R56")
@pytest.mark.asyncio
async def test_should_notify_nobody_when_auto_group_settings_change(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy", gender="female")
    gid = await _auto_group(client, admin)

    renamed = await client.patch(
        f"/v1/groups/by_id/{gid}", json={"name": "Renamed"}, headers=auth(admin)
    )

    assert renamed.status_code == 200, renamed.text
    fetched = await client.get(f"/v1/groups/by_id/{gid}", headers=auth(admin))
    assert fetched.json()["name"] == "Renamed"
    assert await notifications_for(db_session, "amy", "group.settings_changed") == []


@pytest.mark.requirement("notifications:R76")
@pytest.mark.asyncio
async def test_should_skip_member_when_enrollment_is_terminal(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    _ = await create_member_user(db_session, "bob")
    venue = await create_venue(client, admin)
    event_id = (await create_programme(client, admin, venue))["id"]
    assert (await assign(client, admin, event_id, "amy", "bob")).status_code == 204
    removed = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )
    assert removed.status_code == 204, removed.text
    assert await enrollment_of(client, admin, event_id, "amy") == "removed"

    deleted = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(admin))

    assert deleted.status_code == 200, deleted.text
    assert len(await notifications_for(db_session, "bob", "event.deleted")) == 1
    assert await notifications_for(db_session, "amy", "event.deleted") == []


@pytest.mark.requirement("notifications:R77")
@pytest.mark.asyncio
async def test_should_notify_change_audience_when_programme_is_terminated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    venue = await create_venue(client, admin)
    start = at(days=2)
    event_id = (await create_programme(client, admin, venue, start=start))["id"]
    assert (await assign(client, admin, event_id, "amy")).status_code == 204
    cutoff = start + 5 * DAY_MS

    response = await terminate(client, admin, event_id, cutoff, reason="Rink closed")

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, event_id))["untilTimeUtc"] == cutoff
    for recipient in ("amy", "admin"):
        rows = await notifications_for(db_session, recipient, "event.terminated")
        assert len(rows) == 1, recipient
        data = rows[0].payload["data"]
        assert data["eventId"] == event_id
        assert data["reason"] == "Rink closed"
        assert data["cutoffTimeUtc"] == cutoff


@pytest.mark.requirement("notifications:R105")
@pytest.mark.asyncio
async def test_should_notify_user_when_forgotten_password_is_reset(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    user = (
        await db_session.execute(select(User).where(User.username == "amy"))
    ).scalar_one()
    user.email = "amy@example.com"
    await db_session.commit()
    ConsoleEmailSender.clear()

    response = await client.post(
        "/v1/auth/reset-password", json={"email": "amy@example.com"}
    )

    assert response.status_code == 204, response.text
    assert [m.to for m in ConsoleEmailSender.outbox] == ["amy@example.com"]
    ConsoleEmailSender.clear()
    rows = await notifications_for(db_session, "amy", "account.password_changed_self")
    assert len(rows) == 1
    assert rows[0].payload["data"] == {"username": "amy"}
