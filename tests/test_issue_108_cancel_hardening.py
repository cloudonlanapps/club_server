"""Tests for #108 — hardened Camp event-cancel and occurrence-cancel.

Covers: required request fields (all types), Camp-only session-boundary /
lead-time / past rules with super-admin bypass + notification suppression, the
"cancel from occurrence N keeps N-1" boundary semantics, the extended cancel
audience, and the no-longer-cascading enrollment behavior.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

DAY_MS = 86_400_000
MIN_MS = 60_000


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def floor_ms(dt: datetime) -> int:
    return (int(dt.timestamp() * 1000) // 1000) * 1000


def at(*, hours: float = 0) -> int:
    return floor_ms(datetime.now(timezone.utc) + timedelta(hours=hours))


async def _venue(client: AsyncClient, token: str) -> int:
    r = await client.post("/v1/venues", json={"name": "V"}, headers=auth(token))
    return r.json()["id"]


async def _camp(
    client: AsyncClient,
    token: str,
    *,
    start: int,
    count: int = 5,
    organizer: str | None = None,
    coaches: list[str] | None = None,
) -> int:
    venue_id = await _venue(client, token)
    body = {
        "title": "Camp",
        "type": "camp",
        "visibility": "public",
        "venueId": venue_id,
        "startTimeUtc": start,
        "endTimeUtc": start + 3600_000,
        "rrule": f"FREQ=DAILY;COUNT={count}",
    }
    if organizer is not None:
        body["organizerName"] = organizer
    if coaches is not None:
        body["coachNames"] = coaches
    r = await client.post("/v1/events", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _notifs(db: AsyncSession, username: str, ntype: str) -> list[Notification]:
    db.expire_all()
    res = await db.execute(
        select(Notification).where(
            Notification.username == username, Notification.type == ntype
        )
    )
    return list(res.scalars().all())


# ---------------------------------------------------------------------------
# required fields (all event types)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_cancel_requires_effective_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id = await _camp(client, admin, start=at(hours=48))
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1},
        headers=auth(admin),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_occurrence_cancel_requires_reason(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(client, admin, start=start)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start + 2 * DAY_MS}/cancel",
        json={},
        headers=auth(admin),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Rule 1 — session boundary (unconditional)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camp_cancel_rejects_non_boundary_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(client, admin, start=start)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={
            "reason": "x",
            "version": 1,
            "effectiveDateTimeUtc": start + 49 * 3600_000,
        },
        headers=auth(admin),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY"


@pytest.mark.asyncio
async def test_camp_occurrence_cancel_rejects_non_boundary(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(client, admin, start=start)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start + 3600_000}/cancel",
        # Any version: a slot that is not an occurrence is refused first.
        json={"version": 1, "reason": "x"},
        headers=auth(admin),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "OCCURRENCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# boundary semantics — cancel from the 3rd occurrence keeps two
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camp_cancel_from_third_occurrence_keeps_two(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(client, admin, start=start, count=5)

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1, "effectiveDateTimeUtc": start + 2 * DAY_MS},
        headers=auth(admin),
    )
    assert resp.status_code == 200

    listing = await client.get(
        f"/v1/events/occurrences?fromTimeUtc={at(hours=0)}&toTimeUtc={at(hours=24 * 10)}",
        headers=auth(admin),
    )
    occs = sorted(
        (o for o in listing.json() if o["eventId"] == event_id),
        key=lambda o: o["startTimeUtc"],
    )
    assert len(occs) == 5
    statuses = [o["status"] for o in occs]
    assert statuses == ["scheduled", "scheduled", "cancelled", "cancelled", "cancelled"]
    assert sum(1 for o in occs if o["status"] == "scheduled") == 2


# ---------------------------------------------------------------------------
# Rule 2 / Rule 3 — lead-time & past, with super-admin bypass + suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camp_cancel_lead_time_violated_for_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    start = at(hours=0) + 10 * MIN_MS  # first occurrence ~10 min out (within lead)
    event_id = await _camp(client, super_admin, start=start)

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1, "effectiveDateTimeUtc": start},
        headers=auth(admin),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "CANCELLATION_LEAD_TIME_VIOLATED"


@pytest.mark.asyncio
async def test_camp_cancel_past_rejected_for_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    start = at(hours=-72)  # camp started 3 days ago
    event_id = await _camp(client, super_admin, start=start, count=10)

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1, "effectiveDateTimeUtc": start + DAY_MS},
        headers=auth(admin),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "EFFECTIVE_TIME_IN_PAST"


@pytest.mark.requirement("notifications:R84")
@pytest.mark.asyncio
async def test_camp_cancel_super_admin_bypasses_past_and_suppresses_notification(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    start = at(hours=-72)
    event_id = await _camp(client, super_admin, start=start, count=10)
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(super_admin),
    )

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1, "effectiveDateTimeUtc": start + DAY_MS},
        headers=auth(super_admin),
    )
    assert resp.status_code == 200
    # Override path is a testing escape hatch → notification suppressed.
    assert await _notifs(db_session, "amy", "event.cancelled") == []


# ---------------------------------------------------------------------------
# audience + enrollment preservation
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R75")
@pytest.mark.asyncio
async def test_camp_cancel_notifies_enrollees_coaches_and_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    await create_member_user(db_session, "olive")  # organizer
    await create_coach_user(db_session, "carl")
    start = at(hours=48)
    event_id = await _camp(
        client, admin, start=start, organizer="olive", coaches=["carl"]
    )
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1, "effectiveDateTimeUtc": start + 2 * DAY_MS},
        headers=auth(admin),
    )
    assert resp.status_code == 200
    for u in ("amy", "olive", "carl"):
        assert len(await _notifs(db_session, u, "event.cancelled")) == 1, f"{u} missing"


@pytest.mark.asyncio
async def test_camp_cancel_preserves_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    start = at(hours=48)
    event_id = await _camp(client, admin, start=start)
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "x", "version": 1, "effectiveDateTimeUtc": start + 2 * DAY_MS},
        headers=auth(admin),
    )
    assert resp.status_code == 200

    status = await client.get(
        f"/v1/myevents/by_id/amy/{event_id}/enrollments", headers=auth(admin)
    )
    assert status.json()["status"] == "assigned"
