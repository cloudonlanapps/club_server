"""Optimistic locking on events (#292).

Every event carries a ``version`` that every mutation bumps, and
``updatedBy`` naming who made it. Update, correction and split must send
the version the client last saw: a missing one is 422, a stale one is 409
``STALE_VERSION`` whose body carries the current ``version``, ``updatedAt``
and ``updatedBy`` so the app can say who changed the event before asking
the user to reload. Cancel, undo-cancel, delete and restore take no
version but bump it, so a client editing across one of them is told.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user, create_regular_admin_user
from .redesign_helpers import (
    at,
    auth,
    cancel_series,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    get_event,
)

DAY_MS = 24 * 60 * 60 * 1000


async def _setup(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str, int]:
    admin = await create_admin_user(db_session)
    second = await create_regular_admin_user(db_session, "second_admin")
    venue = await create_venue(client, admin)
    return admin, second, venue


def _stale_body(response) -> dict:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STALE_VERSION"
    return detail


# --- the field --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_start_at_version_one_when_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)

    event = await create_camp(client, admin, venue)

    assert event["version"] == 1
    assert event["updatedBy"] == "admin"
    fetched = await get_event(client, admin, event["id"])
    assert fetched["version"] == 1
    assert fetched["updatedBy"] == "admin"


# --- update (camp / one-off) ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_update_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    event = await create_camp(client, admin, venue)

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"title": "Renamed"},
        headers=auth(admin),
    )
    assert response.status_code == 422, response.text

    fetched = await get_event(client, admin, event["id"])
    assert fetched["title"] == event["title"]
    assert fetched["version"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_update_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    event = await create_camp(client, admin, venue)

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"title": "Renamed", "version": 1},
        headers=auth(second),
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.json()["updatedBy"] == "second_admin"

    fetched = await get_event(client, admin, event["id"])
    assert fetched["title"] == "Renamed"
    assert fetched["version"] == 2
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_reject_update_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    event = await create_camp(client, admin, venue)
    first = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"title": "Theirs", "version": 1},
        headers=auth(second),
    )
    assert first.status_code == 200, first.text

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"title": "Mine", "version": 1},
        headers=auth(admin),
    )

    detail = _stale_body(response)
    assert detail["version"] == 2
    assert detail["updatedBy"] == "second_admin"
    assert detail["updatedAt"] == first.json()["updatedAtUtc"]
    fetched = await get_event(client, admin, event["id"])
    assert fetched["title"] == "Theirs"
    assert fetched["version"] == 2


# --- correction (programme) ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_correction_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    event = await create_programme(client, admin, venue)

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}/correction",
        json={"title": "Renamed"},
        headers=auth(admin),
    )
    assert response.status_code == 422, response.text
    assert (await get_event(client, admin, event["id"]))["version"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_correction_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    event = await create_programme(client, admin, venue)

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}/correction",
        json={"title": "Renamed", "version": 1},
        headers=auth(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2

    fetched = await get_event(client, admin, event["id"])
    assert fetched["title"] == "Renamed"
    assert fetched["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_reject_correction_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    event = await create_programme(client, admin, venue)
    first = await client.patch(
        f"/v1/events/by_id/{event['id']}/correction",
        json={"description": "Theirs", "version": 1},
        headers=auth(second),
    )
    assert first.status_code == 200, first.text

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}/correction",
        json={"description": "Mine", "version": 1},
        headers=auth(admin),
    )

    detail = _stale_body(response)
    assert detail["version"] == 2
    assert detail["updatedBy"] == "second_admin"
    assert (await get_event(client, admin, event["id"]))["description"] == "Theirs"


# --- split (programme) --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_split_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    _ = await create_coach_user(db_session, "ann")
    start = at(days=2)
    event = await create_programme(client, admin, venue, start=start)

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}/future",
        json={"effectiveDateTimeUtc": start + DAY_MS, "coachNames": ["ann"]},
        headers=auth(admin),
    )
    assert response.status_code == 422, response.text
    assert (await get_event(client, admin, event["id"]))["version"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_split_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    _ = await create_coach_user(db_session, "ann")
    start = at(days=2)
    event = await create_programme(client, admin, venue, start=start)

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}/future",
        json={
            "effectiveDateTimeUtc": start + DAY_MS,
            "coachNames": ["ann"],
            "version": 1,
        },
        headers=auth(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert (await get_event(client, admin, event["id"]))["coachNames"] == ["ann"]


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_reject_split_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    _ = await create_coach_user(db_session, "ann")
    start = at(days=2)
    event = await create_programme(client, admin, venue, start=start)
    first = await client.patch(
        f"/v1/events/by_id/{event['id']}/correction",
        json={"title": "Theirs", "version": 1},
        headers=auth(second),
    )
    assert first.status_code == 200, first.text

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}/future",
        json={
            "effectiveDateTimeUtc": start + DAY_MS,
            "coachNames": ["ann"],
            "version": 1,
        },
        headers=auth(admin),
    )

    detail = _stale_body(response)
    assert detail["version"] == 2
    assert detail["updatedBy"] == "second_admin"
    schedules = await client.get(
        f"/v1/events/by_id/{event['id']}/schedules", headers=auth(admin)
    )
    assert len(schedules.json()) == 1


# --- mutations that take no version still bump it ---------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_series_cancelled_and_undone(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    start = at(days=2)
    event = await create_camp(client, admin, venue, start=start)

    cancelled = await cancel_series(client, second, event["id"], start + DAY_MS)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["version"] == 2
    assert cancelled.json()["updatedBy"] == "second_admin"

    undone = await client.post(
        f"/v1/events/by_id/{event['id']}/undo-cancel", headers=auth(admin)
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["version"] == 3
    assert undone.json()["updatedBy"] == "admin"
    assert (await get_event(client, admin, event["id"]))["version"] == 3


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_deleted_and_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    event = await create_camp(client, admin, venue)

    deleted = await client.delete(
        f"/v1/events/by_id/{event['id']}", headers=auth(second)
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["version"] == 2
    assert deleted.json()["updatedBy"] == "second_admin"

    restored = await client.post(
        f"/v1/events/by_id/{event['id']}/restore", headers=auth(admin)
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    assert restored.json()["updatedBy"] == "admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_update_when_event_was_cancelled_in_between(
    client: AsyncClient, db_session: AsyncSession
):
    """The bump on cancel is what tells an editor the event moved under them."""
    admin, second, venue = await _setup(client, db_session)
    start = at(days=2)
    event = await create_camp(client, admin, venue, start=start)
    cancelled = await cancel_series(client, second, event["id"], start + DAY_MS)
    assert cancelled.status_code == 200, cancelled.text

    response = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"title": "Mine", "version": 1},
        headers=auth(admin),
    )

    detail = _stale_body(response)
    assert detail["version"] == 2
    assert detail["updatedBy"] == "second_admin"


# --- in-place reschedule (camp / one-off, #434) ------------------------------


async def _reschedule(client: AsyncClient, token: str, event_id: int, **body: object):
    return await client.post(
        f"/v1/events/by_id/{event_id}/reschedule", json=body, headers=auth(token)
    )


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_reschedule_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    other_venue = await create_venue(client, admin, "Other rink")
    event = await create_camp(client, admin, venue)

    response = await _reschedule(client, admin, event["id"], venueId=other_venue)

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, event["id"])
    assert fetched["venueId"] == venue
    assert fetched["version"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_reschedule_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    other_venue = await create_venue(client, admin, "Other rink")
    event = await create_camp(client, admin, venue)

    response = await _reschedule(
        client, admin, event["id"], venueId=other_venue, version=1
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    fetched = await get_event(client, admin, event["id"])
    assert fetched["venueId"] == other_venue
    assert fetched["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_reject_reschedule_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    theirs = await create_venue(client, admin, "Their rink")
    mine = await create_venue(client, admin, "My rink")
    event = await create_camp(client, admin, venue)
    first = await _reschedule(client, second, event["id"], venueId=theirs, version=1)
    assert first.status_code == 200, first.text

    response = await _reschedule(client, admin, event["id"], venueId=mine, version=1)

    detail = _stale_body(response)
    assert detail["version"] == 2
    assert detail["updatedBy"] == "second_admin"
    fetched = await get_event(client, admin, event["id"])
    assert fetched["venueId"] == theirs
    assert fetched["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_reject_oneoff_reschedule_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    theirs = await create_venue(client, admin, "Their rink")
    mine = await create_venue(client, admin, "My rink")
    event = await create_oneoff(client, admin, venue)
    first = await _reschedule(client, second, event["id"], venueId=theirs, version=1)
    assert first.status_code == 200, first.text

    response = await _reschedule(client, admin, event["id"], venueId=mine, version=1)

    detail = _stale_body(response)
    assert detail["version"] == 2
    assert detail["updatedBy"] == "second_admin"
    assert (await get_event(client, admin, event["id"]))["venueId"] == theirs


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_bump_version_when_oneoff_reschedule_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    other_venue = await create_venue(client, admin, "Other rink")
    event = await create_oneoff(client, admin, venue)

    response = await _reschedule(
        client, admin, event["id"], venueId=other_venue, version=1
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, event["id"])
    assert fetched["venueId"] == other_venue
    assert fetched["version"] == 2
