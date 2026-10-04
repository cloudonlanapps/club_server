"""Tests for #232 — generic PATCH is metadata-only; schedule fields removed.

`startTimeUtc`, `endTimeUtc`, `rrule`, `venueId` and `type` are no longer part
of `EventUpdate` (which is `extra="forbid"`), so sending any of them to
`PATCH /v1/events/by_id/{event_id}` returns 422 for every event type.
`sessions` was removed with them (#248) and is accepted again as a correction
of record (#423, camp R105, one-off R22a; `test_sessions_correction.py`).
Schedule changes go through `POST /v1/events/by_id/{event_id}/reschedule` (#230).
This
supersedes the interim camp-only 400 `IMMUTABLE_CAMP_FIELD` guard (#112).
Permitted metadata fields still succeed; `untilTimeUtc` stays rejected (#105).
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_ms(hours: int = 24) -> int:
    ms = int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)
    return ms // 1000 * 1000


async def create_venue(client: AsyncClient, token: str, name: str = "V") -> int:
    r = await client.post("/v1/venues", json={"name": name}, headers=auth(token))
    return r.json()["id"]


async def create_event(
    client: AsyncClient, token: str, venue_id: int, type_: str
) -> int:
    start_ms = future_ms(24)
    body: dict = {
        "title": "E",
        "type": type_,
        "venueId": venue_id,
        "startTimeUtc": start_ms,
        "endTimeUtc": start_ms + 4 * 60 * 60 * 1000,
    }
    if type_ == "camp":
        body["rrule"] = "FREQ=DAILY;COUNT=3"
    r = await client.post("/v1/events", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["camp", "oneOff"])
@pytest.mark.parametrize(
    ("payload_key", "value"),
    [
        ("startTimeUtc", future_ms(72)),
        ("endTimeUtc", future_ms(73)),
        ("rrule", "FREQ=DAILY;COUNT=5"),
        ("venueId", 999999),
        ("type", "oneOff"),
    ],
)
async def test_patch_rejects_removed_schedule_field(
    client: AsyncClient,
    db_session: AsyncSession,
    event_type: str,
    payload_key: str,
    value: object,
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id = await create_event(client, token, venue_id, event_type)

    resp = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={payload_key: value, "version": 1},
        headers=auth(token),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["camp", "oneOff"])
async def test_patch_allows_permitted_metadata(
    client: AsyncClient, db_session: AsyncSession, event_type: str
):
    token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "coach_a")
    _ = await create_coach_user(db_session, "coach_b")
    venue_id = await create_venue(client, token, "V1")
    event_id = await create_event(client, token, venue_id, event_type)

    resp = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={
            "title": "Renamed",
            "description": "Updated",
            "coachNames": ["coach_a", "coach_b"],
            "version": 1,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "Renamed"
    assert data["coachNames"] == ["coach_a", "coach_b"]


@pytest.mark.asyncio
async def test_patch_mixed_payload_rejected_no_partial_apply(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id = await create_event(client, token, venue_id, "camp")

    resp = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"title": "Should Not Apply", "startTimeUtc": future_ms(72), "version": 1},
        headers=auth(token),
    )
    assert resp.status_code == 422

    # Whole body rejected by the schema — the permitted field is not applied.
    get_resp = await client.get(f"/v1/events/by_id/{event_id}", headers=auth(token))
    assert get_resp.json()["title"] == "E"


@pytest.mark.asyncio
async def test_patch_until_time_still_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id = await create_event(client, token, venue_id, "camp")

    resp = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"untilTimeUtc": future_ms(200), "version": 1},
        headers=auth(token),
    )
    assert resp.status_code == 422
