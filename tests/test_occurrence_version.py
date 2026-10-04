"""An occurrence's own version (#430): lifecycle L23–L23b.

Two staff moving the same occurrence used to overwrite each other silently.
Every occurrence now carries a ``version``; reschedule, cancel, undo-cancel,
drop and reinstate require it and refuse a stale one with 409.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user
from .redesign_helpers import (
    HOUR_MS,
    at,
    auth,
    cancel_occurrence,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    get_event,
    reinstate,
    reschedule_occurrence,
    undo_cancel_occurrence,
    version_of,
)


async def _occurrence(
    client: AsyncClient, token: str, event_id: int, slot: int
) -> dict:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _programme(client: AsyncClient, token: str) -> tuple[int, int, int]:
    """A programme two days out; returns (event id, first slot, venue id)."""
    venue = await create_venue(client, token)
    start = at(days=2)
    programme = await create_programme(client, token, venue, start=start)
    return programme["id"], start, venue


# ---------------------------------------------------------------------------
# L23: every occurrence has a version; every change bumps it
# ---------------------------------------------------------------------------


@pytest.mark.requirement("lifecycle:L23")
@pytest.mark.asyncio
async def test_should_report_version_one_when_occurrence_was_never_changed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)

    occurrence = await _occurrence(client, admin, event_id, slot)

    assert occurrence["version"] == 1
    assert occurrence["updatedAt"] is None
    assert occurrence["updatedBy"] is None


@pytest.mark.requirement("lifecycle:L23")
@pytest.mark.asyncio
async def test_should_report_the_version_in_the_occurrence_listing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)
    moved = await reschedule_occurrence(
        client, admin, event_id, slot, newStartTimeUtc=slot + HOUR_MS
    )
    assert moved.status_code == 204, moved.text

    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": slot - HOUR_MS, "toTimeUtc": slot + 3 * HOUR_MS},
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    (listed,) = [o for o in response.json() if o["occurrenceTimeUtc"] == slot]
    assert listed["version"] == 2
    assert listed["updatedBy"] == "admin"


@pytest.mark.requirement("lifecycle:L23")
@pytest.mark.asyncio
async def test_should_bump_version_and_record_actor_when_occurrence_is_rescheduled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)
    before = at()

    first = await reschedule_occurrence(
        client, admin, event_id, slot, version=1, newStartTimeUtc=slot + HOUR_MS
    )
    assert first.status_code == 204, first.text
    after_first = await _occurrence(client, admin, event_id, slot)
    second = await reschedule_occurrence(
        client, admin, event_id, slot, version=2, newStartTimeUtc=slot + 2 * HOUR_MS
    )

    assert second.status_code == 204, second.text
    assert after_first["version"] == 2
    assert after_first["updatedBy"] == "admin"
    assert after_first["updatedAt"] >= before
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["version"] == 3
    assert occurrence["startTimeUtc"] == slot + 2 * HOUR_MS


@pytest.mark.requirement("lifecycle:L23")
@pytest.mark.asyncio
async def test_should_bump_version_when_occurrence_is_cancelled_and_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)

    cancelled = await cancel_occurrence(client, admin, event_id, slot, version=1)
    assert cancelled.status_code == 204, cancelled.text
    after_cancel = await _occurrence(client, admin, event_id, slot)
    restored = await undo_cancel_occurrence(client, admin, event_id, slot, version=2)

    assert restored.status_code == 204, restored.text
    assert after_cancel["version"] == 2
    assert after_cancel["status"] == "cancelled"
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["version"] == 3
    assert occurrence["status"] == "scheduled"
    assert occurrence["updatedBy"] == "admin"


@pytest.mark.requirement("lifecycle:L23")
@pytest.mark.asyncio
async def test_should_bump_occurrence_version_not_event_version_when_oneoff_is_dropped(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)
    slot = oneoff["startTimeUtc"]

    dropped = await drop(client, admin, oneoff["id"], version=1)
    assert dropped.status_code == 200, dropped.text
    after_drop = await _occurrence(client, admin, oneoff["id"], slot)
    reinstated = await reinstate(client, admin, oneoff["id"], version=2)

    assert reinstated.status_code == 200, reinstated.text
    assert after_drop["version"] == 2
    assert after_drop["status"] == "cancelled"
    occurrence = await _occurrence(client, admin, oneoff["id"], slot)
    assert occurrence["version"] == 3
    assert occurrence["status"] == "scheduled"
    fetched = await get_event(client, admin, oneoff["id"])
    assert fetched["version"] == oneoff["version"]


# ---------------------------------------------------------------------------
# L23a: the version is required, and a stale one is refused
# ---------------------------------------------------------------------------


@pytest.mark.requirement("lifecycle:L23a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "body"),
    [
        ("reschedule", {"newVenueId": 1}),
        ("cancel", {"reason": "Ice not ready"}),
        ("undo-cancel", {}),
    ],
)
async def test_should_reject_occurrence_change_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession, verb: str, body: dict
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/{verb}",
        json=body,
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert any(e["loc"][-1] == "version" for e in response.json()["detail"])
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["version"] == 1
    assert occurrence["status"] == "scheduled"


@pytest.mark.requirement("lifecycle:L23a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "body"), [("drop", {"reason": "Off"}), ("reinstate", {})]
)
async def test_should_reject_drop_or_reinstate_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession, verb: str, body: dict
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)

    response = await client.post(
        f"/v1/events/by_id/{oneoff['id']}/{verb}", json=body, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert any(e["loc"][-1] == "version" for e in response.json()["detail"])
    occurrence = await _occurrence(client, admin, oneoff["id"], oneoff["startTimeUtc"])
    assert occurrence["version"] == 1
    assert occurrence["status"] == "scheduled"


@pytest.mark.requirement("lifecycle:L23a")
@pytest.mark.asyncio
async def test_should_refuse_second_venue_move_when_it_carries_the_version_both_saw(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)
    rink_x = await create_venue(client, admin, "Rink X")
    rink_y = await create_venue(client, admin, "Rink Y")

    first = await reschedule_occurrence(
        client, admin, event_id, slot, version=1, newVenueId=rink_x
    )
    assert first.status_code == 204, first.text
    second = await reschedule_occurrence(
        client, admin, event_id, slot, version=1, newVenueId=rink_y
    )

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "STALE_VERSION"
    assert detail["version"] == 2
    assert detail["updatedBy"] == "admin"
    assert detail["updatedAt"] is not None
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["venueId"] == rink_x
    assert occurrence["version"] == 2


@pytest.mark.requirement("lifecycle:L23a")
@pytest.mark.asyncio
async def test_should_refuse_cancel_when_occurrence_moved_since_it_was_read(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)
    moved = await reschedule_occurrence(
        client, admin, event_id, slot, version=1, newStartTimeUtc=slot + HOUR_MS
    )
    assert moved.status_code == 204, moved.text

    response = await cancel_occurrence(client, admin, event_id, slot, version=1)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "STALE_VERSION"
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["status"] == "rescheduled"
    assert occurrence["version"] == 2


@pytest.mark.requirement("lifecycle:L23a")
@pytest.mark.asyncio
async def test_should_refuse_version_ahead_of_an_unchanged_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)

    response = await cancel_occurrence(client, admin, event_id, slot, version=2)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STALE_VERSION"
    assert detail["version"] == 1
    assert detail["updatedAt"] is None
    assert detail["updatedBy"] is None
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["status"] == "scheduled"


@pytest.mark.requirement("lifecycle:L23a")
@pytest.mark.asyncio
async def test_should_refuse_reinstate_when_it_carries_the_event_version(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)
    dropped = await drop(client, admin, oneoff["id"], version=1)
    assert dropped.status_code == 200, dropped.text

    # The event is still at version 1; its occurrence has moved to 2.
    response = await reinstate(client, admin, oneoff["id"], version=1)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "STALE_VERSION"
    occurrence = await _occurrence(client, admin, oneoff["id"], oneoff["startTimeUtc"])
    assert occurrence["status"] == "cancelled"


# ---------------------------------------------------------------------------
# L23b: a version never goes backwards
# ---------------------------------------------------------------------------


@pytest.mark.requirement("lifecycle:L23b")
@pytest.mark.asyncio
async def test_should_refuse_the_pre_cancel_version_after_cancel_and_undo(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, slot, _ = await _programme(client, admin)
    assert (await cancel_occurrence(client, admin, event_id, slot)).status_code == 204
    assert (
        await undo_cancel_occurrence(client, admin, event_id, slot)
    ).status_code == 204

    # A client that read the occurrence before either change still holds 1.
    response = await reschedule_occurrence(
        client, admin, event_id, slot, version=1, newStartTimeUtc=slot + HOUR_MS
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["version"] == 3
    occurrence = await _occurrence(client, admin, event_id, slot)
    assert occurrence["startTimeUtc"] == slot


@pytest.mark.requirement("lifecycle:L23b")
@pytest.mark.asyncio
async def test_should_keep_the_version_moving_when_in_place_reschedule_resets_overrides(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    slot = camp["startTimeUtc"]
    assert (await cancel_occurrence(client, admin, camp["id"], slot)).status_code == 204
    elsewhere = await create_venue(client, admin, "Second rink")

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/reschedule",
        json={
            "version": await version_of(client, admin, camp["id"]),
            "venueId": elsewhere,
            "resetOverrides": True,
        },
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    occurrence = await _occurrence(client, admin, camp["id"], slot)
    assert occurrence["status"] == "scheduled"
    assert occurrence["version"] == 3
    assert occurrence["venueId"] == elsewhere


@pytest.mark.requirement("lifecycle:L23b")
@pytest.mark.asyncio
async def test_should_allow_in_place_reschedule_when_the_only_row_was_undone(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    slot = camp["startTimeUtc"]
    assert (await cancel_occurrence(client, admin, camp["id"], slot)).status_code == 204
    assert (
        await undo_cancel_occurrence(client, admin, camp["id"], slot)
    ).status_code == 204
    elsewhere = await create_venue(client, admin, "Second rink")

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/reschedule",
        json={
            "version": await version_of(client, admin, camp["id"]),
            "venueId": elsewhere,
        },
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["venueId"] == elsewhere
