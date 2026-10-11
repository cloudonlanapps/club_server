"""A programme's cutoff verbs take the event's version (#13, lifecycle L22a,
L22b).

Terminate, extend and extend-indefinitely each replace the programme's
cutoff. Two admins acting on the same programme at once, one extending and
one terminating, used to overwrite each other silently; each verb now sends
the version the client last saw: a missing one is 422, a stale one is 409
``STALE_VERSION`` naming who moved the event and when, and nothing is
written. ``test_cutoff_version_camp.py`` covers cancel and undo-cancel.
"""

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    at,
    auth,
    create_programme,
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


async def _terminated_programme(
    client: AsyncClient, admin: str, venue: int
) -> tuple[dict, int, int]:
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 5 * DAY_MS
    response = await _verb(
        client,
        admin,
        programme["id"],
        "terminate",
        reason="Season over",
        cutoffTimeUtc=cutoff,
        version=programme["version"],
    )
    assert response.status_code == 200, response.text
    return response.json(), start, cutoff


# --- terminate (programme) ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_terminate_programme_and_bump_version_when_version_is_current(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 5 * DAY_MS

    response = await _verb(
        client,
        second,
        programme["id"],
        "terminate",
        reason="Season over",
        cutoffTimeUtc=cutoff,
        version=1,
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_refuse_terminate_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    moved = await _rename(client, second, programme, "/correction")

    response = await _verb(
        client,
        admin,
        programme["id"],
        "terminate",
        reason="Season over",
        cutoffTimeUtc=start + 5 * DAY_MS,
        version=1,
    )

    _assert_stale(response, moved)
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None
    assert fetched["version"] == 2
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_terminate_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await _verb(
        client,
        admin,
        programme["id"],
        "terminate",
        reason="Season over",
        cutoffTimeUtc=start + 5 * DAY_MS,
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None
    assert fetched["version"] == 1


# --- extend (programme) ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_extend_programme_and_bump_version_when_version_is_current(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    terminated, start, _ = await _terminated_programme(client, admin, venue)
    assert terminated["version"] == 2

    response = await _verb(
        client,
        second,
        terminated["id"],
        "extend",
        cutoffTimeUtc=start + 8 * DAY_MS,
        version=2,
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 3
    fetched = await get_event(client, admin, terminated["id"])
    assert fetched["untilTimeUtc"] == start + 8 * DAY_MS
    assert fetched["version"] == 3
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_refuse_extend_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    terminated, start, cutoff = await _terminated_programme(client, admin, venue)

    response = await _verb(
        client,
        admin,
        terminated["id"],
        "extend",
        cutoffTimeUtc=start + 8 * DAY_MS,
        version=1,
    )

    _assert_stale(response, terminated)
    fetched = await get_event(client, admin, terminated["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_extend_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    terminated, start, cutoff = await _terminated_programme(client, admin, venue)

    response = await _verb(
        client, admin, terminated["id"], "extend", cutoffTimeUtc=start + 8 * DAY_MS
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, terminated["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2


# --- extend-indefinitely (programme) -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22")
async def test_should_extend_indefinitely_and_bump_version_when_version_is_current(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, venue = await _setup(client, db_session)
    terminated, _, _ = await _terminated_programme(client, admin, venue)

    response = await _verb(
        client, second, terminated["id"], "extend-indefinitely", version=2
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 3
    fetched = await get_event(client, admin, terminated["id"])
    assert fetched["untilTimeUtc"] is None
    assert fetched["version"] == 3
    assert fetched["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22b")
async def test_should_refuse_extend_indefinitely_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    terminated, _, cutoff = await _terminated_programme(client, admin, venue)

    response = await _verb(
        client, admin, terminated["id"], "extend-indefinitely", version=1
    )

    _assert_stale(response, terminated)
    fetched = await get_event(client, admin, terminated["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("lifecycle:L22a")
async def test_should_reject_extend_indefinitely_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, venue = await _setup(client, db_session)
    terminated, _, cutoff = await _terminated_programme(client, admin, venue)

    response = await _verb(
        client, admin, terminated["id"], "extend-indefinitely", reason="Running on"
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, terminated["id"])
    assert fetched["untilTimeUtc"] == cutoff
    assert fetched["version"] == 2
