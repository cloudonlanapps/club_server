"""Tests for #113 — tightened Camp single-occurrence reschedule.

Covers the reshaped request (duration instead of end-time, organizer dropped,
at-least-one-field rule), the Camp-only past/lead-time rules with super-admin
bypass + notification suppression, venue/cancelled checks, server-derived end
time, and the sparse `changes` notification payload.
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
from .redesign_helpers import occurrence_version

DAY_MS = 86_400_000
MIN_MS = 60_000


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def floor_ms(dt: datetime) -> int:
    return (int(dt.timestamp() * 1000) // 1000) * 1000


def at(*, hours: float = 0, minutes: float = 0) -> int:
    return floor_ms(
        datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes)
    )


async def _venue(client: AsyncClient, token: str) -> int:
    r = await client.post("/v1/venues", json={"name": "V"}, headers=auth(token))
    return r.json()["id"]


async def _camp(
    client: AsyncClient,
    token: str,
    *,
    start: int,
    venue_id: int,
    organizer: str | None = None,
    coaches: list[str] | None = None,
) -> int:
    body = {
        "title": "Camp",
        "type": "camp",
        "visibility": "public",
        "venueId": venue_id,
        "startTimeUtc": start,
        "endTimeUtc": start + 3600_000,  # 60-minute sessions
        "rrule": "FREQ=DAILY;COUNT=5",
    }
    if organizer is not None:
        body["organizerName"] = organizer
    if coaches is not None:
        body["coachNames"] = coaches
    r = await client.post("/v1/events", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _reschedule(client: AsyncClient, token: str, event_id: int, occ: int, **body):
    body.setdefault("version", await occurrence_version(client, token, event_id, occ))
    return await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/reschedule",
        json=body,
        headers=auth(token),
    )


async def _notifs(db: AsyncSession, username: str, ntype: str) -> list[Notification]:
    db.expire_all()
    res = await db.execute(
        select(Notification).where(
            Notification.username == username, Notification.type == ntype
        )
    )
    return list(res.scalars().all())


# ---------------------------------------------------------------------------
# request shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_empty_body_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(
        client, admin, start=start, venue_id=await _venue(client, admin)
    )
    resp = await _reschedule(client, admin, event_id, start + 2 * DAY_MS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "NOTHING_TO_RESCHEDULE"


@pytest.mark.asyncio
async def test_reschedule_only_organizer_is_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    # newOrganizerName was dropped from the schema. It is now rejected outright
    # (#325) rather than discarded and treated as an empty reschedule.
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(
        client, admin, start=start, venue_id=await _venue(client, admin)
    )
    resp = await _reschedule(
        client, admin, event_id, start + 2 * DAY_MS, newOrganizerName="someone"
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        err["type"] == "extra_forbidden" and err["loc"][-1] == "newOrganizerName"
        for err in detail
    ), detail


@pytest.mark.asyncio
async def test_reschedule_duration_out_of_range_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(
        client, admin, start=start, venue_id=await _venue(client, admin)
    )
    for bad in (0, 1441):
        resp = await _reschedule(
            client, admin, event_id, start + 2 * DAY_MS, newDurationMinutes=bad
        )
        assert resp.status_code == 422, bad


# ---------------------------------------------------------------------------
# Camp past / lead-time rules + super-admin bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_past_start_rejected_for_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(
        client, super_admin, start=start, venue_id=await _venue(client, super_admin)
    )
    resp = await _reschedule(
        client, admin, event_id, start + 2 * DAY_MS, newStartTimeUtc=at(hours=-1)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "POSTPONE_ONLY"


@pytest.mark.asyncio
async def test_reschedule_lead_time_violated_for_admin(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    start = at(minutes=10)  # first occurrence ~10 min out (within lead)
    event_id = await _camp(
        client, super_admin, start=start, venue_id=await _venue(client, super_admin)
    )
    resp = await _reschedule(
        client, admin, event_id, start, newStartTimeUtc=at(hours=48)
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "RESCHEDULE_LEAD_TIME_VIOLATED"


@pytest.mark.requirement("notifications:R84")
@pytest.mark.asyncio
async def test_reschedule_super_admin_bypasses_lead_time_and_suppresses(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    start = at(minutes=10)
    event_id = await _camp(
        client, super_admin, start=start, venue_id=await _venue(client, super_admin)
    )
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(super_admin),
    )
    resp = await _reschedule(
        client, super_admin, event_id, start, newStartTimeUtc=at(hours=48)
    )
    assert resp.status_code == 204
    assert await _notifs(db_session, "amy", "occurrence.rescheduled") == []


@pytest.mark.asyncio
async def test_rereschedule_uses_effective_start_not_original_slot(
    client: AsyncClient, db_session: AsyncSession
):
    """#283 — a second reschedule measures lead-time against the occurrence's
    effective (overridden) start, not its now-imminent original slot.

    The first occurrence sits inside the lead window, so a super admin moves it
    far into the future (bypassing the guard). The day now genuinely happens in
    48h, so a regular admin re-reschedule — keyed by the original slot — must be
    allowed; the guard must not treat the stale original slot as imminent.
    """
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await _venue(client, super_admin)
    start = at(minutes=10)  # original slot within the lead window
    event_id = await _camp(client, super_admin, start=start, venue_id=venue)

    moved = await _reschedule(
        client, super_admin, event_id, start, newStartTimeUtc=at(hours=48)
    )
    assert moved.status_code == 204, moved.text

    # Effective start is +48h (far outside lead); re-reschedule must succeed.
    again = await _reschedule(
        client, admin, event_id, start, newStartTimeUtc=at(hours=72)
    )
    assert again.status_code == 204, again.text


@pytest.mark.asyncio
async def test_rereschedule_duration_only_keeps_moved_start(
    client: AsyncClient, db_session: AsyncSession
):
    """#283 — a duration-only second reschedule derives the new end from the
    occurrence's overridden start, not its original slot."""
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await _venue(client, super_admin)
    start = at(minutes=10)
    moved_start = at(hours=48)
    event_id = await _camp(client, super_admin, start=start, venue_id=venue)

    moved = await _reschedule(
        client, super_admin, event_id, start, newStartTimeUtc=moved_start
    )
    assert moved.status_code == 204, moved.text

    # Change only the duration; start stays at the moved time and the end is
    # moved_start + 120 min (not the original slot + 120 min).
    again = await _reschedule(client, admin, event_id, start, newDurationMinutes=120)
    assert again.status_code == 204, again.text

    got = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start}", headers=auth(admin)
    )
    body = got.json()
    assert body["startTimeUtc"] == moved_start
    assert body["endTimeUtc"] - body["startTimeUtc"] == 120 * MIN_MS


# ---------------------------------------------------------------------------
# venue / cancelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_unknown_venue_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    event_id = await _camp(
        client, admin, start=start, venue_id=await _venue(client, admin)
    )
    resp = await _reschedule(
        client, admin, event_id, start + 2 * DAY_MS, newVenueId=999999
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "VENUE_NOT_FOUND"


@pytest.mark.asyncio
async def test_reschedule_cancelled_occurrence_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    occ = start + 2 * DAY_MS
    event_id = await _camp(
        client, admin, start=start, venue_id=await _venue(client, admin)
    )
    await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/cancel",
        json={
            "version": await occurrence_version(client, admin, event_id, occ),
            "reason": "x",
        },
        headers=auth(admin),
    )
    resp = await _reschedule(client, admin, event_id, occ, newDurationMinutes=90)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


# ---------------------------------------------------------------------------
# server-derived end time + sparse changes payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_duration_sets_end_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    start = at(hours=48)
    occ = start + 2 * DAY_MS
    event_id = await _camp(
        client, admin, start=start, venue_id=await _venue(client, admin)
    )

    resp = await _reschedule(client, admin, event_id, occ, newDurationMinutes=120)
    assert resp.status_code == 204

    got = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}", headers=auth(admin)
    )
    body = got.json()
    # Start unchanged (duration-only); end = occurrence start + 120 min.
    assert body["startTimeUtc"] == occ
    assert body["endTimeUtc"] - body["startTimeUtc"] == 120 * MIN_MS
    assert body["isRescheduled"] is True


@pytest.mark.requirement("notifications:R81")
@pytest.mark.asyncio
async def test_reschedule_notification_uses_sparse_changes(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    start = at(hours=48)
    occ = start + 2 * DAY_MS
    venue_id = await _venue(client, admin)
    event_id = await _camp(client, admin, start=start, venue_id=venue_id)
    venue2 = await _venue(client, admin)
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    resp = await _reschedule(
        client, admin, event_id, occ, newDurationMinutes=90, newVenueId=venue2
    )
    assert resp.status_code == 204

    rows = await _notifs(db_session, "amy", "occurrence.rescheduled")
    assert len(rows) == 1
    changes = rows[0].payload["data"]["changes"]
    assert changes == {"durationMinutes": 90, "venueId": venue2}


@pytest.mark.requirement("notifications:R81")
@pytest.mark.asyncio
async def test_reschedule_notifies_enrollees_coaches_and_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    await create_member_user(db_session, "olive")
    await create_coach_user(db_session, "carl")
    start = at(hours=48)
    occ = start + 2 * DAY_MS
    event_id = await _camp(
        client,
        admin,
        start=start,
        venue_id=await _venue(client, admin),
        organizer="olive",
        coaches=["carl"],
    )
    await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["amy"]},
        headers=auth(admin),
    )

    resp = await _reschedule(client, admin, event_id, occ, newDurationMinutes=120)
    assert resp.status_code == 204
    for u in ("amy", "olive", "carl"):
        assert len(await _notifs(db_session, u, "occurrence.rescheduled")) == 1, u
