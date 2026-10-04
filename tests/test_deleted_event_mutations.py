"""A soft-deleted event refuses every mutation with 404 (#471, lifecycle L8).

Every type loads the event it mutates through one loader, which treats a
soft-deleted event as absent. Before #471 only the programme verbs did, so a
deleted camp or one-off could still be cancelled, rescheduled or edited —
and its members were notified.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    auth,
    cancel_series,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    get_event,
    notifications_for,
    version_of,
)


async def _delete(client: AsyncClient, token: str, event_id: int) -> None:
    response = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["deletedAtUtc"] is not None


async def _restore(client: AsyncClient, token: str, event_id: int) -> None:
    response = await client.post(
        f"/v1/events/by_id/{event_id}/restore", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["deletedAtUtc"] is None


def _assert_event_not_found(response) -> None:
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"


async def _patch(client: AsyncClient, token: str, event_id: int, version: int):
    return await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"version": version, "title": "Renamed"},
        headers=auth(token),
    )


async def _reschedule(
    client: AsyncClient, token: str, event_id: int, start: int, version: int
):
    return await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={"version": version, "startTimeUtc": start, "endTimeUtc": start + HOUR_MS},
        headers=auth(token),
    )


# --- camp ---------------------------------------------------------------


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_cancelling_a_deleted_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "skater")
    await db_session.commit()
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    assigned = await assign(client, admin, camp["id"], "skater")
    assert assigned.status_code in (200, 204), assigned.text
    await _delete(client, admin, camp["id"])

    response = await cancel_series(client, admin, camp["id"], start)

    _assert_event_not_found(response)
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["untilTimeUtc"] is None
    assert await notifications_for(db_session, "skater", "event.cancelled") == []


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_cancel_a_camp_when_it_is_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    await _delete(client, admin, camp["id"])
    await _restore(client, admin, camp["id"])

    response = await cancel_series(client, admin, camp["id"], start)

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, camp["id"]))["untilTimeUtc"] == start


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_undoing_the_cancel_of_a_deleted_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    cancelled = await cancel_series(client, admin, camp["id"], start)
    assert cancelled.status_code == 200, cancelled.text
    await _delete(client, admin, camp["id"])

    response = await client.post(
        f"/v1/events/by_id/{camp['id']}/undo-cancel", headers=auth(admin)
    )

    _assert_event_not_found(response)
    assert (await get_event(client, admin, camp["id"]))["untilTimeUtc"] == start


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_rescheduling_a_deleted_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    await _delete(client, admin, camp["id"])
    version = await version_of(client, admin, camp["id"])

    response = await _reschedule(client, admin, camp["id"], start + HOUR_MS, version)

    _assert_event_not_found(response)
    assert (await get_event(client, admin, camp["id"]))["startTimeUtc"] == start


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_updating_a_deleted_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    await _delete(client, admin, camp["id"])
    version = await version_of(client, admin, camp["id"])

    response = await _patch(client, admin, camp["id"], version)

    _assert_event_not_found(response)
    assert (await get_event(client, admin, camp["id"]))["title"] == camp["title"]


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_update_a_camp_when_it_is_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    await _delete(client, admin, camp["id"])
    await _restore(client, admin, camp["id"])
    version = await version_of(client, admin, camp["id"])

    response = await _patch(client, admin, camp["id"], version)

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, camp["id"]))["title"] == "Renamed"


# --- one-off ------------------------------------------------------------


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_rescheduling_a_deleted_oneoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    await _delete(client, admin, oneoff["id"])
    version = await version_of(client, admin, oneoff["id"])

    response = await _reschedule(client, admin, oneoff["id"], start + HOUR_MS, version)

    _assert_event_not_found(response)
    assert (await get_event(client, admin, oneoff["id"]))["startTimeUtc"] == start


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_reschedule_a_oneoff_when_it_is_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, admin, venue, start=start)
    await _delete(client, admin, oneoff["id"])
    await _restore(client, admin, oneoff["id"])
    version = await version_of(client, admin, oneoff["id"])

    response = await _reschedule(client, admin, oneoff["id"], start + HOUR_MS, version)

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, oneoff["id"])
    assert fetched["startTimeUtc"] == start + HOUR_MS


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_updating_a_deleted_oneoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)
    await _delete(client, admin, oneoff["id"])
    version = await version_of(client, admin, oneoff["id"])

    response = await _patch(client, admin, oneoff["id"], version)

    _assert_event_not_found(response)
    assert (await get_event(client, admin, oneoff["id"]))["title"] == oneoff["title"]


# --- programme ----------------------------------------------------------


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_return_404_when_correcting_a_deleted_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    await _delete(client, admin, programme["id"])
    version = await version_of(client, admin, programme["id"])

    response = await client.patch(
        f"/v1/events/by_id/{programme['id']}/correction",
        json={"version": version, "title": "Renamed"},
        headers=auth(admin),
    )

    _assert_event_not_found(response)
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["title"] == programme["title"]


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.asyncio
async def test_should_correct_a_programme_when_it_is_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    await _delete(client, admin, programme["id"])
    await _restore(client, admin, programme["id"])
    version = await version_of(client, admin, programme["id"])

    response = await client.patch(
        f"/v1/events/by_id/{programme['id']}/correction",
        json={"version": version, "title": "Renamed"},
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, programme["id"]))["title"] == "Renamed"


# --- event marketing (#484) -----------------------------------------------


def _marketing_url(event_id: int) -> str:
    return f"/v1/events/by_id/{event_id}/marketing"


async def _put_marketing(client: AsyncClient, token: str, event_id: int, fee: int):
    return await client.put(
        _marketing_url(event_id), json={"fee": fee}, headers=auth(token)
    )


async def _marketing_fee(client: AsyncClient, token: str, event_id: int):
    response = await client.get(_marketing_url(event_id), headers=auth(token))
    if response.status_code == 404:
        return None
    assert response.status_code == 200, response.text
    return response.json()["fee"]


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.usefixtures("event_marketing_enabled")
@pytest.mark.asyncio
async def test_should_return_404_when_replacing_marketing_of_a_deleted_event(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    await _delete(client, admin, camp["id"])

    response = await _put_marketing(client, admin, camp["id"], 5000)

    _assert_event_not_found(response)
    assert await _marketing_fee(client, admin, camp["id"]) is None


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.usefixtures("event_marketing_enabled")
@pytest.mark.asyncio
async def test_should_return_404_when_deleting_marketing_of_a_deleted_event(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    assert (await _put_marketing(client, admin, camp["id"], 5000)).status_code == 200
    await _delete(client, admin, camp["id"])

    response = await client.delete(_marketing_url(camp["id"]), headers=auth(admin))

    _assert_event_not_found(response)
    assert await _marketing_fee(client, admin, camp["id"]) == 5000


@pytest.mark.requirement("lifecycle:L8")
@pytest.mark.usefixtures("event_marketing_enabled")
@pytest.mark.asyncio
async def test_should_replace_and_delete_marketing_when_the_event_is_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue)
    await _delete(client, admin, camp["id"])
    await _restore(client, admin, camp["id"])

    replaced = await _put_marketing(client, admin, camp["id"], 5000)
    assert replaced.status_code == 200, replaced.text
    assert await _marketing_fee(client, admin, camp["id"]) == 5000

    deleted = await client.delete(_marketing_url(camp["id"]), headers=auth(admin))
    assert deleted.status_code == 204, deleted.text
    assert await _marketing_fee(client, admin, camp["id"]) is None
