"""Changing a camp's or one-off's organizer or coaches runs the conflict gates (#473).

The generic update (camp R2, one-off R2) runs the gates creation runs when
it changes who staffs the event. A camp or one-off never blocks on a clash
(programme R30c, one-off R20), so every finding is reported to the admins as
an ``event.conflict_detected`` notice, the way creation reports it, and the
update succeeds.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user
from .redesign_helpers import (
    at,
    auth,
    create_camp,
    create_oneoff,
    create_venue,
    get_event,
    notifications_for,
    version_of,
)


async def _conflict_notices(db_session: AsyncSession) -> list:
    return await notifications_for(db_session, "admin", "event.conflict_detected")


async def _patch(client: AsyncClient, token: str, event_id: int, **body: object):
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}", json=body, headers=auth(token)
    )


@pytest.mark.asyncio
async def test_should_report_coach_clash_when_camp_coaches_change(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "busy")
    _ = await create_coach_user(db_session, "org_a")
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    other_venue = await create_venue(client, admin, "Other")
    start = at(days=3)
    held = await create_camp(
        client,
        admin,
        venue,
        start=start,
        count=3,
        organizerName="org_a",
        coachNames=["busy"],
    )
    edited = await create_camp(
        client, admin, other_venue, start=start, count=3, organizerName="org_b"
    )
    assert await _conflict_notices(db_session) == []

    response = await _patch(client, admin, edited["id"], coachNames=["busy"])

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, edited["id"]))["coachNames"] == ["busy"]
    notices = await _conflict_notices(db_session)
    assert len(notices) == 1
    data = notices[0].payload["data"]
    assert data["eventId"] == edited["id"]
    assert [c["eventId"] for c in data["conflictingEvents"]] == [held["id"]]


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.asyncio
async def test_should_report_organizer_clash_without_409_when_oneoff_organizer_changes(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "org_a")
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    other_venue = await create_venue(client, admin, "Other")
    start = at(days=3)
    held = await create_oneoff(client, admin, venue, start=start, organizerName="org_a")
    edited = await create_oneoff(
        client, admin, other_venue, start=start, organizerName="org_b"
    )
    assert await _conflict_notices(db_session) == []

    response = await _patch(client, admin, edited["id"], organizerName="org_a")

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, edited["id"]))["organizerName"] == "org_a"
    notices = await _conflict_notices(db_session)
    assert len(notices) == 1
    data = notices[0].payload["data"]
    assert data["eventId"] == edited["id"]
    assert [c["eventId"] for c in data["conflictingEvents"]] == [held["id"]]


@pytest.mark.asyncio
async def test_should_not_report_when_new_coach_is_free(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "busy")
    _ = await create_coach_user(db_session, "free")
    _ = await create_coach_user(db_session, "org_a")
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    other_venue = await create_venue(client, admin, "Other")
    start = at(days=3)
    _ = await create_camp(
        client,
        admin,
        venue,
        start=start,
        count=3,
        organizerName="org_a",
        coachNames=["busy"],
    )
    edited = await create_camp(
        client, admin, other_venue, start=start, count=3, organizerName="org_b"
    )

    response = await _patch(client, admin, edited["id"], coachNames=["free"])

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, edited["id"]))["coachNames"] == ["free"]
    assert await _conflict_notices(db_session) == []


@pytest.mark.asyncio
async def test_should_not_report_again_when_update_leaves_staff_unchanged(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "busy")
    _ = await create_coach_user(db_session, "org_a")
    _ = await create_coach_user(db_session, "org_b")
    venue = await create_venue(client, admin)
    other_venue = await create_venue(client, admin, "Other")
    start = at(days=3)
    _ = await create_oneoff(
        client, admin, venue, start=start, organizerName="org_a", coachNames=["busy"]
    )
    edited = await create_oneoff(
        client,
        admin,
        other_venue,
        start=start,
        organizerName="org_b",
        coachNames=["busy"],
    )
    assert len(await _conflict_notices(db_session)) == 1

    response = await _patch(
        client,
        admin,
        edited["id"],
        title="Renamed",
        organizerName="org_b",
        coachNames=["busy"],
    )

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, edited["id"]))["title"] == "Renamed"
    assert len(await _conflict_notices(db_session)) == 1
