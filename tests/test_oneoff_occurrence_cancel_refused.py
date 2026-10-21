"""The per-occurrence cancel routes refuse a one-off (#48).

``docs/oneoff_requirements.md`` R8a. A one-off is called off with ``/drop``
and brought back with ``/reinstate`` (R3–R7). ``…/occurrences/{t}/cancel``
and ``…/undo-cancel`` refuse it with 422 ``INVALID_STATE`` and name the
route to use, as the series cancel does, so a one-off has one way in and
one way out — and ``undo-cancel`` cannot bring back a one-off that has
already started (R7).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    audit_rows,
    auth,
    cancel_occurrence,
    create_oneoff,
    create_venue,
    drop,
    list_user_occurrences,
    undo_cancel_occurrence,
)


async def _status(client: AsyncClient, token: str, event_id: int, slot: int) -> str:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["status"]


@pytest.mark.requirement("oneoff:R8a")
@pytest.mark.asyncio
async def test_should_refuse_occurrence_cancel_when_event_is_oneoff(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)
    assert (await assign(client, admin, oneoff["id"], "skater")).status_code == 204

    response = await cancel_occurrence(client, admin, oneoff["id"], start)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_STATE"
    assert "/drop" in detail["message"]
    assert await _status(client, admin, oneoff["id"], start) == "scheduled"
    mine = await list_user_occurrences(
        client,
        member,
        "skater",
        start - HOUR_MS,
        start + HOUR_MS,
        event_id=oneoff["id"],
    )
    assert [o["status"] for o in mine] == ["scheduled"]
    assert await audit_rows(db_session, "cancel_occurrence") == []


@pytest.mark.requirement("oneoff:R8a")
@pytest.mark.asyncio
async def test_should_refuse_occurrence_undo_cancel_when_event_is_oneoff(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)
    assert (await drop(client, admin, oneoff["id"])).status_code == 200

    response = await undo_cancel_occurrence(client, admin, oneoff["id"], start)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_STATE"
    assert "/reinstate" in detail["message"]
    assert await _status(client, admin, oneoff["id"], start) == "cancelled"


@pytest.mark.requirement("oneoff:R8a")
@pytest.mark.requirement("oneoff:R7")
@pytest.mark.asyncio
async def test_should_refuse_super_admin_undo_cancel_when_oneoff_has_started(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(minutes=-10)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)
    assert (await drop(client, super_admin, oneoff["id"])).status_code == 200

    response = await undo_cancel_occurrence(client, super_admin, oneoff["id"], start)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    assert await _status(client, super_admin, oneoff["id"], start) == "cancelled"
