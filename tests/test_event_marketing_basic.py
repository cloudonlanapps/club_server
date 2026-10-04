"""Basic marketing fields on the event (#409, marketing R1–R4).

``shortDescription``, ``stamp``, ``highlights`` and ``includes`` are core
event fields: cheap, type-agnostic, and what every club's public site puts
on an event card. They travel on create, update and correction, and come
back on every event read.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _venue(client: AsyncClient, token: str) -> int:
    response = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=_auth(token)
    )
    assert response.status_code == 201
    return response.json()["id"]


def _future(hours: int) -> int:
    from datetime import datetime, timedelta, timezone

    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


BASIC = {
    "shortDescription": "Six days of power on ice",
    "stamp": "Christmas Camp!",
    "highlights": ["Skating posture", "Puck handling"],
    "includes": ["Full gear", "Certificate"],
}


async def _create(
    client: AsyncClient, token: str, venue_id: int, *, event_type: str = "camp", **extra
) -> dict:
    start = _future(48)
    body = {
        "title": "Camp",
        "type": event_type,
        "venueId": venue_id,
        "visibility": "public",
        "startTimeUtc": start,
        "endTimeUtc": start + 3_600_000,
        **({"rrule": "FREQ=WEEKLY;BYDAY=SA"} if event_type == "programme" else {}),
        **extra,
    }
    response = await client.post("/v1/events", json=body, headers=_auth(token))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R1")
async def test_should_round_trip_basic_fields_on_create_get_and_list(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue_id = await _venue(client, admin)

    created = await _create(client, admin, venue_id, **BASIC)
    for key, value in BASIC.items():
        assert created[key] == value

    fetched = await client.get(
        f"/v1/events/by_id/{created['id']}", headers=_auth(admin)
    )
    assert fetched.status_code == 200
    for key, value in BASIC.items():
        assert fetched.json()[key] == value

    listed = await client.get("/v1/events", headers=_auth(admin))
    assert listed.status_code == 200
    item = next(e for e in listed.json()["items"] if e["id"] == created["id"])
    assert item["highlights"] == BASIC["highlights"]


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R2")
async def test_should_default_basic_fields_to_null_when_omitted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue_id = await _venue(client, admin)

    created = await _create(client, admin, venue_id)
    assert created["shortDescription"] is None
    assert created["stamp"] is None
    assert created["highlights"] is None
    assert created["includes"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R3")
async def test_should_update_basic_fields_on_camp_and_clear_with_null(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue_id = await _venue(client, admin)
    created = await _create(client, admin, venue_id, **BASIC)

    response = await client.patch(
        f"/v1/events/by_id/{created['id']}",
        json={"version": created["version"], "stamp": "Sold out", "includes": None},
        headers=_auth(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stamp"] == "Sold out"
    assert body["includes"] is None
    assert body["highlights"] == BASIC["highlights"]

    fetched = await client.get(
        f"/v1/events/by_id/{created['id']}", headers=_auth(admin)
    )
    assert fetched.json()["stamp"] == "Sold out"
    assert fetched.json()["includes"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R3")
async def test_should_correct_basic_fields_on_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue_id = await _venue(client, admin)
    created = await _create(client, admin, venue_id, event_type="programme")

    response = await client.patch(
        f"/v1/events/by_id/{created['id']}/correction",
        json={"version": created["version"], **BASIC},
        headers=_auth(admin),
    )
    assert response.status_code == 200, response.text
    for key, value in BASIC.items():
        assert response.json()[key] == value

    fetched = await client.get(
        f"/v1/events/by_id/{created['id']}", headers=_auth(admin)
    )
    assert fetched.json()["shortDescription"] == BASIC["shortDescription"]


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R4")
async def test_should_reject_malformed_basic_fields(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue_id = await _venue(client, admin)
    await db_session.commit()
    start = _future(48)
    base = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "visibility": "public",
        "startTimeUtc": start,
        "endTimeUtc": start + 3_600_000,
    }
    for bad, error_type in (
        ({"highlights": "not a list"}, "list_type"),
        ({"includes": [1, 2]}, "string_type"),
        ({"shortDescription": "x" * 301}, "string_too_long"),
        ({"stamp": "x" * 61}, "string_too_long"),
        ({"highlights": ["x"] * 21}, "too_long"),
    ):
        response = await client.post(
            "/v1/events", json={**base, **bad}, headers=_auth(admin)
        )
        assert response.status_code == 422, bad
        # The field itself is validated, not refused as an unknown key.
        assert error_type in str(response.json()["detail"]), (bad, response.text)
