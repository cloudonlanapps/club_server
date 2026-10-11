"""A camp's cutoff verbs take the event's version (#13, lifecycle L22a, L22b).

Cancel and undo-cancel each replace the camp's cutoff. Two admins acting on
the same camp at once used to overwrite each other silently; each verb now
sends the version the client last saw: a missing one is 422, a stale one is
409 ``STALE_VERSION`` naming who moved the event and when, and nothing is
written. ``test_cutoff_version_programme.py`` covers terminate and extend.
"""

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    at,
    auth,
    create_camp,
    create_venue,
    get_event,
)


async def _setup(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str, int]:
    admin = await create_admin_user(db_session)
    second = await create_regular_admin_user(db_session, "second_admin")
    venue = await create_venue(client, admin)
    return admin, second, venue


async def _verb(
    client: AsyncClient, token: str, event_id: int, verb: str, **body: object
) -> Response:
    return await client.post(
        f"/v1/events/by_id/{event_id}/{verb}", json=body, headers=auth(token)
    )


async def _rename(
    client: AsyncClient, token: str, event: dict, route: str = ""
) -> dict:
    """Another editor moves the event on by one version."""
    response = await client.patch(
        f"/v1/events/by_id/{event['id']}{route}",
        json={"title": "Theirs", "version": event["version"]},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_stale(response: Response, moved: dict) -> None:
    """409 ``STALE_VERSION`` carrying where ``moved`` left the event."""
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STALE_VERSION"
    assert detail["version"] == moved["version"]
    assert detail["updatedAt"] == moved["updatedAtUtc"]
    assert detail["updatedBy"] == moved["updatedBy"]


async def _cancelled_camp(
    client: AsyncClient, admin: str, venue: int
) -> tuple[dict, int]:
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    cutoff = start + DAY_MS
    response = await _verb(
        client,
        admin,
        camp["id"],
        "cancel",
        reason="Weather",
        effectiveDateTimeUtc=cutoff,
        version=camp["version"],
    )
    assert response.status_code == 200, response.text
    return response.json(), cutoff


# --- cancel (camp) -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_cancel_camp_and_bump_version_when_version_is_current(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)

    response = await _verb(
        client,
        second,
        camp["id"],
        "cancel",
        reason="Weather",
        effectiveDateTimeUtc=start + DAY_MS,
        version=1,
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["untilTimeUtc"] == start + DAY_MS
    assert fetched["version"] == 2
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_refuse_camp_cancel_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)
    moved = await _rename(client, second, camp)

    response = await _verb(
        client,
        admin,
        camp["id"],
        "cancel",
        reason="Weather",
        effectiveDateTimeUtc=start + DAY_MS,
        version=1,
    )

    _assert_stale(response, moved)
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["untilTimeUtc"] is None
    assert fetched["version"] == 2
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_camp_cancel_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)

    response = await _verb(
        client,
        admin,
        camp["id"],
        "cancel",
        reason="Weather",
        effectiveDateTimeUtc=start + DAY_MS,
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["untilTimeUtc"] is None
    assert fetched["version"] == 1


# --- undo-cancel (camp) ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_undo_camp_cancel_and_bump_version_when_version_is_current(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    cancelled, _ = await _cancelled_camp(client, admin, venue)
    assert cancelled["version"] == 2

    response = await _verb(client, second, cancelled["id"], "undo-cancel", version=2)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 3
    fetched = await get_event(client, admin, cancelled["id"])
    assert fetched["untilTimeUtc"] is None
    assert fetched["version"] == 3
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_refuse_camp_undo_cancel_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    cancelled, cutoff = await _cancelled_camp(client, admin, venue)

    response = await _verb(client, admin, cancelled["id"], "undo-cancel", version=1)

    _assert_stale(response, cancelled)
    fetched = await get_event(client, admin, cancelled["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_camp_undo_cancel_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    cancelled, cutoff = await _cancelled_camp(client, admin, venue)

    no_body = await client.post(
        f"/v1/events/by_id/{cancelled['id']}/undo-cancel", headers=auth(admin)
    )
    empty_body = await _verb(client, admin, cancelled["id"], "undo-cancel")

    assert no_body.status_code == 422, no_body.text
    assert empty_body.status_code == 422, empty_body.text
    fetched = await get_event(client, admin, cancelled["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2
