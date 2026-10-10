"""The marketing block has its own version (#13, marketing R13, R13a).

``PUT`` replaces the whole row, so two editors working on the same event's
page used to wipe out each other's fields. The block carries a ``version``,
``updatedAt`` and ``updatedBy`` of its own, on an occurrence's terms: with no
row it counts as version 1 and every write bumps it. ``PUT`` and ``DELETE``
send the version the client last saw: a missing one is 422, a stale one is
409 ``STALE_VERSION`` naming who wrote last and when, and nothing is written.
The event's own version is not involved.
"""

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.utils import generate_event_public_id

from .helpers import create_admin_user, create_regular_admin_user
from .redesign_helpers import auth, create_camp, create_venue, get_event

pytestmark = pytest.mark.usefixtures("event_marketing_enabled")


async def _setup(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str, str]:
    """Two admins and the marketing URL of a public camp."""
    admin = await create_admin_user(db_session)
    second = await create_regular_admin_user(db_session, "second_admin")
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, visibility="public")
    return admin, second, f"/v1/events/by_id/{camp['id']}/marketing"


def _event_id(url: str) -> int:
    return int(url.split("/")[-2])


async def _put(client: AsyncClient, token: str, url: str, **body: object) -> Response:
    return await client.put(url, json=body, headers=auth(token))


async def _delete(
    client: AsyncClient, token: str, url: str, version: int | None
) -> Response:
    params = {} if version is None else {"version": version}
    return await client.delete(url, params=params, headers=auth(token))


async def _stored(client: AsyncClient, token: str, url: str) -> dict | None:
    response = await client.get(url, headers=auth(token))
    if response.status_code == 404:
        return None
    assert response.status_code == 200, response.text
    return response.json()


def _stale_detail(response: Response) -> dict:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "STALE_VERSION"
    return detail


# --- PUT ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13")
async def test_should_write_marketing_at_version_two_when_first_put_sends_version_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, url = await _setup(client, db_session)
    assert await _stored(client, admin, url) is None

    response = await _put(client, second, url, fee=100, version=1)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2
    assert body["updatedBy"] == "second_admin"
    assert isinstance(body["updatedAt"], int)
    stored = await _stored(client, admin, url)
    assert stored is not None
    assert stored["fee"] == 100
    assert stored["version"] == 2
    assert stored["updatedBy"] == "second_admin"
    assert stored["updatedAt"] == body["updatedAt"]


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13")
async def test_should_bump_marketing_version_when_put_sends_the_current_version(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, url = await _setup(client, db_session)
    first = await _put(client, second, url, fee=100, version=1)
    assert first.status_code == 200, first.text

    response = await _put(client, admin, url, fee=200, version=2)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 3
    assert response.json()["updatedBy"] == "admin"
    stored = await _stored(client, admin, url)
    assert stored is not None
    assert stored["fee"] == 200
    assert stored["version"] == 3
    assert stored["updatedBy"] == "admin"


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13a")
async def test_should_refuse_marketing_put_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, url = await _setup(client, db_session)
    theirs = await _put(
        client, second, url, fee=100, urgencyText="Limited seats", version=1
    )
    assert theirs.status_code == 200, theirs.text

    response = await _put(client, admin, url, fee=999, version=1)

    detail = _stale_detail(response)
    assert detail["version"] == 2
    assert detail["updatedAt"] == theirs.json()["updatedAt"]
    assert detail["updatedBy"] == "second_admin"
    stored = await _stored(client, admin, url)
    assert stored is not None
    assert stored["fee"] == 100
    assert stored["urgencyText"] == "Limited seats"
    assert stored["version"] == 2
    assert stored["updatedBy"] == "second_admin"


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13a")
async def test_should_refuse_first_marketing_put_when_version_is_not_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, url = await _setup(client, db_session)

    response = await _put(client, admin, url, fee=100, version=2)

    detail = _stale_detail(response)
    assert detail["version"] == 1
    assert detail["updatedAt"] is None
    assert detail["updatedBy"] is None
    assert await _stored(client, admin, url) is None


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13a")
async def test_should_reject_marketing_put_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, url = await _setup(client, db_session)

    on_no_row = await _put(client, admin, url, fee=100)

    assert on_no_row.status_code == 422, on_no_row.text
    assert await _stored(client, admin, url) is None

    written = await _put(client, admin, url, fee=100, version=1)
    assert written.status_code == 200, written.text
    on_a_row = await _put(client, admin, url, fee=999)

    assert on_a_row.status_code == 422, on_a_row.text
    stored = await _stored(client, admin, url)
    assert stored is not None
    assert stored["fee"] == 100
    assert stored["version"] == 2


# --- DELETE ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13")
async def test_should_remove_marketing_when_delete_sends_the_current_version(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, url = await _setup(client, db_session)
    written = await _put(client, admin, url, fee=100, version=1)
    assert written.status_code == 200, written.text

    response = await _delete(client, second, url, 2)

    assert response.status_code == 204, response.text
    assert await _stored(client, admin, url) is None


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13")
async def test_should_count_marketing_as_version_one_when_it_has_no_row(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, url = await _setup(client, db_session)

    absent = await _delete(client, admin, url, 1)

    assert absent.status_code == 204, absent.text
    assert await _stored(client, admin, url) is None
    written = await _put(client, admin, url, fee=100, version=1)
    assert written.status_code == 200, written.text
    assert written.json()["version"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13a")
async def test_should_refuse_marketing_delete_and_write_nothing_when_version_is_stale(
    client: AsyncClient, db_session: AsyncSession
):
    admin, second, url = await _setup(client, db_session)
    first = await _put(client, admin, url, fee=100, version=1)
    assert first.status_code == 200, first.text
    theirs = await _put(client, second, url, fee=200, version=2)
    assert theirs.status_code == 200, theirs.text

    response = await _delete(client, admin, url, 2)

    detail = _stale_detail(response)
    assert detail["version"] == 3
    assert detail["updatedAt"] == theirs.json()["updatedAt"]
    assert detail["updatedBy"] == "second_admin"
    stored = await _stored(client, admin, url)
    assert stored is not None
    assert stored["fee"] == 200
    assert stored["version"] == 3


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13a")
async def test_should_reject_marketing_delete_when_version_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, url = await _setup(client, db_session)
    written = await _put(client, admin, url, fee=100, version=1)
    assert written.status_code == 200, written.text

    response = await _delete(client, admin, url, None)

    assert response.status_code == 422, response.text
    stored = await _stored(client, admin, url)
    assert stored is not None
    assert stored["fee"] == 100
    assert stored["version"] == 2


# --- what the marketing version is not ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13")
async def test_should_leave_event_version_alone_when_marketing_is_written_and_removed(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, url = await _setup(client, db_session)
    event_id = _event_id(url)
    before = await get_event(client, admin, event_id)
    assert before["version"] == 1

    written = await _put(client, admin, url, fee=100, version=1)
    assert written.status_code == 200, written.text
    after_write = await get_event(client, admin, event_id)
    assert after_write["version"] == 1
    assert after_write["updatedAtUtc"] == before["updatedAtUtc"]

    removed = await _delete(client, admin, url, 2)
    assert removed.status_code == 204, removed.text
    assert (await get_event(client, admin, event_id))["version"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R13")
async def test_should_keep_version_and_author_out_of_the_public_marketing_block(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, url = await _setup(client, db_session)
    written = await _put(client, admin, url, fee=100, version=1)
    assert written.status_code == 200, written.text

    response = await client.get(
        f"/v1/public/events/{generate_event_public_id(_event_id(url))}/marketing"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fee"] == 100
    assert "version" not in body
    assert "updatedAt" not in body
    assert "updatedBy" not in body
