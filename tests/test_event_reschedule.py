"""Tests for #230 — Camp series reschedule endpoint.

`POST /v1/events/by_id/{event_id}/reschedule` mutates the schedule fields that
#112 locks out of the generic PATCH, but only before the series starts and only
when no occurrence override would be orphaned. These tests cover the happy path,
both before-start guards (first occurrence past, attendance exists), the
override abort/reset branches, the camp-only guard, and the enrollee
notification.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.attendance import AttendanceRecord
from club_server.db.models.enrollment import Enrollment, EnrollmentStatus
from club_server.db.models.event import Event
from club_server.db.models.notification import Notification
from club_server.db.models.occurrence_override import OccurrenceOverride
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_member_user
from .redesign_helpers import occurrence_version, version_of

DAY_MS = 24 * 60 * 60 * 1000


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_ms(hours: int = 24) -> int:
    # Align to a whole second: occurrence expansion (via dateutil) works at
    # second precision, so sub-second millis would not match generated slots.
    ms = int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)
    return ms // 1000 * 1000


async def create_venue(client: AsyncClient, token: str, name: str = "V") -> int:
    r = await client.post("/v1/venues", json={"name": name}, headers=auth(token))
    return r.json()["id"]


async def create_camp(
    client: AsyncClient,
    token: str,
    venue_id: int,
    *,
    start_hours: int = 24,
    days: int = 3,
) -> tuple[int, int, int]:
    """Create a future camp. Returns (event_id, start_ms, end_ms)."""
    start_ms = future_ms(start_hours)
    end_ms = start_ms + 4 * 60 * 60 * 1000
    r = await client.post(
        "/v1/events",
        json={
            "title": "Camp",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": f"FREQ=DAILY;COUNT={days}",
        },
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], start_ms, end_ms


@pytest.mark.asyncio
async def test_reschedule_future_camp_updates_in_place(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    venue2_id = await create_venue(client, token, "V2")
    event_id, _, _ = await create_camp(client, token, venue_id, start_hours=24, days=3)

    new_start = future_ms(72)
    new_end = new_start + 5 * 60 * 60 * 1000
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": new_start,
            "endTimeUtc": new_end,
            "rrule": "FREQ=DAILY;COUNT=4",
            "venueId": venue2_id,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == event_id
    assert data["startTimeUtc"] == new_start
    assert data["endTimeUtc"] == new_end
    assert data["rrule"] == "FREQ=DAILY;COUNT=4"
    assert data["venueId"] == venue2_id

    # The new schedule materialises 4 occurrences and no overrides remain.
    occ = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": future_ms(0), "toTimeUtc": future_ms(24 * 14)},
        headers=auth(token),
    )
    occs = occ.json()
    assert len(occs) == 4
    assert all(o["status"] == "scheduled" for o in occs)
    assert occs[0]["startTimeUtc"] == new_start


@pytest.mark.asyncio
async def test_reschedule_after_start_fails(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, _, _ = await create_camp(client, token, venue_id, start_hours=24, days=3)

    # Drag the series into the past so the first occurrence has already started.
    event = await db_session.get(Event, event_id)
    assert event is not None
    event.start_time = future_ms(-48)
    event.end_time = future_ms(-44)
    await db_session.flush()

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": future_ms(96),
        },
        headers=auth(token),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "EVENT_ALREADY_STARTED"


@pytest.mark.asyncio
async def test_reschedule_blocked_when_attendance_exists(
    client: AsyncClient, db_session: AsyncSession
):
    """Even with the first occurrence still in the future, an existing
    attendance record (opened within the 30-min lead window) blocks the
    reschedule — attendance is keyed by occurrence time and must not orphan."""
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, _ = await create_camp(
        client, token, venue_id, start_hours=24, days=3
    )

    db_session.add(
        AttendanceRecord(
            event_id=event_id,
            occurrence_time_utc=start_ms,
            membername="admin",
            status="present",
            recorded_at=now_utc_ms(),
        )
    )
    await db_session.flush()

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": future_ms(96),
        },
        headers=auth(token),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "EVENT_ALREADY_STARTED"


@pytest.mark.asyncio
async def test_reschedule_aborts_when_overrides_present(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, _ = await create_camp(
        client, token, venue_id, start_hours=24, days=3
    )

    day2 = start_ms + DAY_MS
    ov = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{day2}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, day2),
            "newStartTimeUtc": day2 + 2 * 60 * 60 * 1000,
        },
        headers=auth(token),
    )
    assert ov.status_code == 204

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": future_ms(96),
        },
        headers=auth(token),
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "OCCURRENCE_OVERRIDES_PRESENT"
    assert day2 in detail["occurrenceTimeUtcs"]

    # Event is unchanged and the override survives.
    db_session.expire_all()
    event = await db_session.get(Event, event_id)
    assert event is not None and event.start_time == start_ms
    override_count = await db_session.scalar(
        select(func.count())
        .select_from(OccurrenceOverride)
        .where(OccurrenceOverride.event_id == event_id)
    )
    assert override_count == 1


@pytest.mark.asyncio
async def test_reschedule_with_reset_overrides_wipes_and_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, _ = await create_camp(
        client, token, venue_id, start_hours=24, days=3
    )

    day2 = start_ms + DAY_MS
    ov = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{day2}/reschedule",
        json={
            "version": await occurrence_version(client, token, event_id, day2),
            "newStartTimeUtc": day2 + 2 * 60 * 60 * 1000,
        },
        headers=auth(token),
    )
    assert ov.status_code == 204

    new_start = future_ms(96)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": new_start,
            "resetOverrides": True,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["startTimeUtc"] == new_start

    # The reset clears each row rather than deleting it, so the occurrence's
    # version keeps moving (#430, lifecycle L23b); none still overrides.
    db_session.expire_all()
    override_count = await db_session.scalar(
        select(func.count())
        .select_from(OccurrenceOverride)
        .where(
            OccurrenceOverride.event_id == event_id,
            OccurrenceOverride.status != "scheduled",
        )
    )
    assert override_count == 0


@pytest.mark.asyncio
async def test_reschedule_non_camp_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    start_ms = future_ms(24)
    r = await client.post(
        "/v1/events",
        json={
            "title": "Prog",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": start_ms + 3600 * 1000,
            "rrule": "FREQ=WEEKLY;BYDAY=MO",
        },
        headers=auth(token),
    )
    event_id = r.json()["id"]

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": future_ms(96),
        },
        headers=auth(token),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "EVENT_TYPE_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_reschedule_empty_payload_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, _, _ = await create_camp(client, token, venue_id)

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={"version": await version_of(client, token, event_id)},
        headers=auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.requirement("notifications:R70")
@pytest.mark.asyncio
async def test_reschedule_notifies_enrolled_members(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, _ = await create_camp(
        client, token, venue_id, start_hours=24, days=3
    )

    now = now_utc_ms()
    db_session.add(
        Enrollment(
            membername="alice",
            event_id=event_id,
            status=EnrollmentStatus.accepted.value,
            created_at=now,
            updated_at=now,
            enrolled_at=now,
        )
    )
    await db_session.flush()

    new_start = future_ms(96)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": new_start,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.username == "alice",
                    Notification.type == "event.rescheduled",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    changes = rows[0].payload["data"]["changes"]
    assert changes["start_time"]["to"] == new_start


# ---------------------------------------------------------------------------
# oneOff events (#232 — /reschedule generalised beyond camps)
# ---------------------------------------------------------------------------


async def create_oneoff(
    client: AsyncClient, token: str, venue_id: int, *, start_hours: int = 24
) -> tuple[int, int, int]:
    """Create a future oneOff. Returns (event_id, start_ms, end_ms)."""
    start_ms = future_ms(start_hours)
    end_ms = start_ms + 2 * 60 * 60 * 1000
    r = await client.post(
        "/v1/events",
        json={
            "title": "OneOff",
            "type": "oneOff",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
        },
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], start_ms, end_ms


@pytest.mark.asyncio
async def test_reschedule_oneoff_in_place(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    venue2_id = await create_venue(client, token, "V2")
    event_id, start_ms, end_ms = await create_oneoff(client, token, venue_id)

    new_start = future_ms(72)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": new_start,
            "venueId": venue2_id,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["startTimeUtc"] == new_start
    assert data["venueId"] == venue2_id
    # Only the start moved → end shifts by the same delta (duration preserved).
    assert data["endTimeUtc"] == end_ms + (new_start - start_ms)


@pytest.mark.asyncio
async def test_reschedule_oneoff_rrule_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, _, _ = await create_oneoff(client, token, venue_id)

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "rrule": "FREQ=DAILY;COUNT=3",
        },
        headers=auth(token),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------------------
# session timetable must stay consistent with the window (#240)
# ---------------------------------------------------------------------------


async def create_camp_with_sessions(
    client: AsyncClient, token: str, venue_id: int
) -> tuple[int, int, int]:
    """Future camp with a 60-min window and two 30-min sessions."""
    start_ms = future_ms(24)
    end_ms = start_ms + 60 * 60 * 1000
    r = await client.post(
        "/v1/events",
        json={
            "title": "Camp",
            "type": "camp",
            "venueId": venue_id,
            "startTimeUtc": start_ms,
            "endTimeUtc": end_ms,
            "rrule": "FREQ=DAILY;COUNT=3",
            "sessions": [
                {"name": "A", "periodMinutes": 30},
                {"name": "B", "periodMinutes": 30},
            ],
        },
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], start_ms, end_ms


@pytest.mark.asyncio
async def test_reschedule_breaking_session_window_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, end_ms = await create_camp_with_sessions(
        client, token, venue_id
    )

    # Stretch the window to 90 min: the 60 min of sessions no longer sums to it.
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "endTimeUtc": start_ms + 90 * 60 * 1000,
        },
        headers=auth(token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"

    # The window is left unchanged (no partial application).
    db_session.expire_all()
    event = await db_session.get(Event, event_id)
    assert event is not None and event.end_time == end_ms


@pytest.mark.asyncio
async def test_reschedule_without_sessions_changes_window_ok(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, _ = await create_camp(client, token, venue_id)  # no sessions

    new_end = start_ms + 6 * 60 * 60 * 1000
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "endTimeUtc": new_end,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["endTimeUtc"] == new_end


@pytest.mark.asyncio
async def test_reschedule_start_only_with_sessions_ok(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, end_ms = await create_camp_with_sessions(
        client, token, venue_id
    )

    # Moving only the start preserves the 60-min duration, so sessions stay valid.
    new_start = future_ms(72)
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "startTimeUtc": new_start,
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["startTimeUtc"] == new_start
    assert data["endTimeUtc"] == end_ms + (new_start - start_ms)


# ---------------------------------------------------------------------------
# atomic window + sessions reschedule (#248)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_window_and_sessions_atomic_ok(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, _ = await create_camp_with_sessions(client, token, venue_id)

    # Stretch the window to 90 min AND supply a new timetable that sums to 90.
    new_end = start_ms + 90 * 60 * 1000
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "endTimeUtc": new_end,
            "sessions": [
                {"name": "A", "periodMinutes": 45},
                {"name": "B", "periodMinutes": 45},
            ],
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["endTimeUtc"] == new_end
    assert data["sessions"] == [
        {"name": "A", "periodMinutes": 45},
        {"name": "B", "periodMinutes": 45},
    ]


@pytest.mark.asyncio
async def test_reschedule_new_sessions_not_summing_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, start_ms, end_ms = await create_camp_with_sessions(
        client, token, venue_id
    )

    # New 90-min window but the supplied sessions still sum to 60 → rejected.
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "endTimeUtc": start_ms + 90 * 60 * 1000,
            "sessions": [
                {"name": "A", "periodMinutes": 30},
                {"name": "B", "periodMinutes": 30},
            ],
        },
        headers=auth(token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"

    # Nothing applied — the original window and timetable survive.
    db_session.expire_all()
    event = await db_session.get(Event, event_id)
    assert event is not None and event.end_time == end_ms


@pytest.mark.asyncio
async def test_reschedule_sessions_only_replaces_timetable(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, _, _ = await create_camp_with_sessions(client, token, venue_id)

    # No window field — just a new timetable that still sums to the 60-min window.
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, token, event_id),
            "sessions": [{"name": "Whole", "periodMinutes": 60}],
        },
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sessions"] == [{"name": "Whole", "periodMinutes": 60}]


@pytest.mark.asyncio
async def test_reschedule_clear_sessions_with_null(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await create_venue(client, token, "V1")
    event_id, _, _ = await create_camp_with_sessions(client, token, venue_id)

    # Explicit null clears the timetable (the only way now that PATCH can't).
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={"version": await version_of(client, token, event_id), "sessions": None},
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sessions"] is None
