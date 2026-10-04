"""Mutation-side tests for Step E of #16.

Camps move to flag-don't-block: venue / organizer / coach / user overlaps
do not block create or join. Camps still require a tighter RRULE
(FREQ=DAILY + bounded). Programmes and oneOff events keep the existing
blocking semantics — regression-guarded here.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user, create_coach_user


def _future_ms(hours: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


async def _create_venue(client: AsyncClient, token: str, name: str = "V") -> int:
    r = await client.post(
        "/v1/venues",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    return r.json()["id"]


async def _create_event(
    client: AsyncClient,
    token: str,
    *,
    venue_id: int,
    title: str,
    type_: str,
    start_hours: int = 24,
    rrule: str | None = "FREQ=DAILY;COUNT=3",
    organizer_name: str | None = None,
    coach_names: list[str] | None = None,
) -> "tuple[int, int]":
    """Return (status_code, event_id or 0)."""
    body: dict = {
        "title": title,
        "type": type_,
        "venueId": venue_id,
        "startTimeUtc": _future_ms(start_hours),
        "endTimeUtc": _future_ms(start_hours + 2),
    }
    if rrule is not None:
        body["rrule"] = rrule
    if organizer_name is not None:
        body["organizerName"] = organizer_name
    if coach_names is not None:
        body["coachNames"] = coach_names
    r = await client.post(
        "/v1/events",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    return r.status_code, (r.json().get("id") if r.status_code == 201 else 0), r


async def _register_user(
    client: AsyncClient, admin_token: str, username: str, db_session: AsyncSession
) -> None:
    await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "testpass123",
            "firstName": "Test",
            "gender": "male",
            "dateOfBirthUtc": 946684800000,
            "phone": "1234567890",
        },
    )
    _pre = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    await attach_identity_document(db_session, username)
    await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {_pre.json()['accessToken']}"},
    )
    await client.post(
        f"/v1/users/by_id/{username}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )


# ---------------------------------------------------------------------------
# Camp creation: flag-don't-block on overlaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camp_create_with_venue_overlap_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)

    sc1, _, _ = await _create_event(
        client, token, venue_id=venue_id, title="Camp A", type_="camp"
    )
    assert sc1 == 201

    sc2, _, r2 = await _create_event(
        client, token, venue_id=venue_id, title="Camp B", type_="camp"
    )
    assert sc2 == 201, r2.text


@pytest.mark.asyncio
async def test_camp_create_with_organizer_overlap_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    v_a = await _create_venue(client, token, "A")
    v_b = await _create_venue(client, token, "B")

    sc1, _, _ = await _create_event(
        client,
        token,
        venue_id=v_a,
        title="Camp A",
        type_="camp",
        organizer_name="admin",
    )
    assert sc1 == 201

    sc2, _, r2 = await _create_event(
        client,
        token,
        venue_id=v_b,
        title="Camp B",
        type_="camp",
        organizer_name="admin",
    )
    assert sc2 == 201, r2.text


@pytest.mark.asyncio
async def test_camp_create_with_coach_overlap_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "bob")
    v_a = await _create_venue(client, token, "A")
    v_b = await _create_venue(client, token, "B")

    sc1, _, _ = await _create_event(
        client,
        token,
        venue_id=v_a,
        title="Camp A",
        type_="camp",
        coach_names=["bob"],
    )
    assert sc1 == 201

    sc2, _, r2 = await _create_event(
        client,
        token,
        venue_id=v_b,
        title="Camp B",
        type_="camp",
        coach_names=["bob"],
    )
    assert sc2 == 201, r2.text


# ---------------------------------------------------------------------------
# Camp RRULE rules: 422 INVALID_RRULE_FOR_CAMP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camp_create_with_weekly_rrule_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)

    sc, _, r = await _create_event(
        client,
        token,
        venue_id=venue_id,
        title="Bad Camp",
        type_="camp",
        rrule="FREQ=WEEKLY;COUNT=4",
    )
    assert sc == 422, r.text
    assert r.json()["detail"]["code"] == "INVALID_RRULE_FOR_CAMP"


@pytest.mark.asyncio
async def test_camp_create_with_unbounded_rrule_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)

    sc, _, r = await _create_event(
        client,
        token,
        venue_id=venue_id,
        title="Unbounded Camp",
        type_="camp",
        rrule="FREQ=DAILY",
    )
    assert sc == 422, r.text
    assert r.json()["detail"]["code"] == "INVALID_RRULE_FOR_CAMP"


@pytest.mark.asyncio
async def test_camp_update_rrule_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """rrule is no longer part of EventUpdate (#232): the generic PATCH rejects
    it with 422. Recurrence changes go through the reschedule endpoint (#230)."""
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)
    sc, event_id, _ = await _create_event(
        client, token, venue_id=venue_id, title="Camp", type_="camp"
    )
    assert sc == 201

    r = await client.patch(
        f"/v1/events/by_id/{event_id}",
        json={"rrule": "FREQ=WEEKLY;COUNT=4", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Camp join: user-overlap is not blocking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camp_join_with_user_overlap_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    """A user enrolled in a time-overlapping event can still be assigned to a camp."""
    token = await create_admin_user(db_session)
    await _register_user(client, token, "alice", db_session)

    # Programme A: alice enrolled
    v_a = await _create_venue(client, token, "A")
    sc, prog_id, r = await _create_event(
        client,
        token,
        venue_id=v_a,
        title="Programme A",
        type_="programme",
        rrule=None,
    )
    assert sc == 201, r.text
    assign = await client.post(
        f"/v1/events/by_id/{prog_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert assign.status_code == 204, assign.text

    # Camp at a different venue but overlapping in time
    v_b = await _create_venue(client, token, "B")
    sc, camp_id, r = await _create_event(
        client, token, venue_id=v_b, title="Camp B", type_="camp"
    )
    assert sc == 201, r.text

    # Assigning alice to the camp must succeed despite the time overlap.
    join = await client.post(
        f"/v1/events/by_id/{camp_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert join.status_code == 204, join.text


# ---------------------------------------------------------------------------
# Programme regression guards: blocking semantics unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_programme_create_with_venue_overlap_still_409(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, token)

    sc1, _, _ = await _create_event(
        client,
        token,
        venue_id=venue_id,
        title="Prog A",
        type_="programme",
        rrule=None,
    )
    assert sc1 == 201

    sc2, _, r = await _create_event(
        client,
        token,
        venue_id=venue_id,
        title="Prog B",
        type_="programme",
        rrule=None,
    )
    assert sc2 == 409, r.text


@pytest.mark.asyncio
async def test_programme_join_with_user_overlap_still_409(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _register_user(client, token, "alice", db_session)

    v_a = await _create_venue(client, token, "A")
    sc, prog_a, r = await _create_event(
        client,
        token,
        venue_id=v_a,
        title="Programme A",
        type_="programme",
        rrule=None,
    )
    assert sc == 201, r.text
    assign = await client.post(
        f"/v1/events/by_id/{prog_a}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert assign.status_code == 204

    v_b = await _create_venue(client, token, "B")
    sc, prog_b, r = await _create_event(
        client,
        token,
        venue_id=v_b,
        title="Programme B",
        type_="programme",
        rrule=None,
        organizer_name="admin",
    )
    # Programme B may also organizer-conflict with prog_a; ensure created
    # cleanly by using a non-overlapping window-organizer combo if needed.
    if sc == 409:
        # Organizer overlap blocked it — retry with a different organizer
        await _register_user(client, token, "bob", db_session)
        sc, prog_b, r = await _create_event(
            client,
            token,
            venue_id=v_b,
            title="Programme B2",
            type_="programme",
            rrule=None,
            organizer_name="bob",
        )
    assert sc == 201, r.text

    join = await client.post(
        f"/v1/events/by_id/{prog_b}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert join.status_code == 409, join.text
    assert join.json()["detail"]["code"] == "TIME_CONFLICT"
