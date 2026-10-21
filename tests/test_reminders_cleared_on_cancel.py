"""Reminders go when their occurrence stops being live (#49).

``docs/notifications_requirements.md`` R87a, R93a. The scans never create a
reminder for a cancelled occurrence (R87, R93); these pin that cancelling
also removes the ones sent before it — one occurrence, a camp series from a
date, a programme's termination, and deleting the event.

Where a reminder's lead time cannot be reached from a test (a slot two days
out has no 24-hour reminder yet), the row is written the way the scan
writes it, through ``NotificationService.notify_for_event``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event import Event
from club_server.services.notification import NotificationEvent, NotificationService
from club_server.services.scheduler import scan_pending_marks, scan_upcoming_events

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    assign,
    at,
    auth,
    cancel_occurrence,
    cancel_series,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    extend,
    now_ms,
    terminate,
    undo_cancel_occurrence,
)

UPCOMING = "event.upcoming_reminder"
PENDING_MARK = "attendance.pending_mark_reminder"


async def _reminded(
    db_session: AsyncSession, username: str, ntype: str, event_id: int
) -> list[int]:
    """Occurrence times ``username`` holds a reminder of ``ntype`` for."""
    from club_server.db.models.notification import Notification

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Notification.payload).where(
                Notification.username == username, Notification.type == ntype
            )
        )
    ).all()
    return sorted(
        p["data"]["occurrenceTimeUtc"]
        for (p,) in rows
        if p["data"]["eventId"] == event_id
    )


async def _remind(
    db_session: AsyncSession, username: str, ntype: str, event_id: int, slot: int
) -> None:
    """Write a reminder row with the payload the scan writes."""
    data: dict[str, object] = {
        "eventId": event_id,
        "eventTitle": "under test",
        "occurrenceTimeUtc": slot,
    }
    if ntype == UPCOMING:
        data["leadHours"] = 24
    else:
        data["occurrenceEndTimeUtc"] = slot + HOUR_MS
    created = await NotificationService(db_session).notify_for_event(
        NotificationEvent(type=ntype, recipients=[username], data=data)
    )
    assert len(created) == 1
    await db_session.commit()


async def _move_to(db_session: AsyncSession, event_id: int, start: int) -> None:
    """Move an event's schedule to ``start``; no endpoint creates one in the past."""
    event = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event.start_time = start
    event.end_time = start + HOUR_MS
    await db_session.commit()
    db_session.expire_all()


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_delete_upcoming_reminder_when_occurrence_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    slot = at(hours=23)
    programme = await create_programme(client, super_admin, venue, start=slot)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    assert await scan_upcoming_events(db_session, now_ms()) == 1
    await db_session.commit()
    assert await _reminded(db_session, "skater", UPCOMING, programme["id"]) == [slot]

    response = await cancel_occurrence(client, admin, programme["id"], slot)

    assert response.status_code == 204, response.text
    assert await _reminded(db_session, "skater", UPCOMING, programme["id"]) == []


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_keep_other_occurrences_reminders_when_one_occurrence_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    first = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=first)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    for slot in (first, first + DAY_MS):
        await _remind(db_session, "skater", UPCOMING, programme["id"], slot)

    response = await cancel_occurrence(client, admin, programme["id"], first)

    assert response.status_code == 204, response.text
    assert await _reminded(db_session, "skater", UPCOMING, programme["id"]) == [
        first + DAY_MS
    ]


@pytest.mark.requirement("notifications:R93a")
@pytest.mark.asyncio
async def test_should_delete_pending_mark_reminder_when_super_admin_cancels_past_session(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    programme = await create_programme(client, super_admin, venue)
    # Two sessions ended in the last day: yesterday's and this morning's.
    earlier = at(days=-1, hours=-3)
    later = earlier + DAY_MS
    await _move_to(db_session, programme["id"], earlier)
    assert await scan_pending_marks(db_session, now_ms()) > 0
    await db_session.commit()
    assert await _reminded(
        db_session, "regular_admin", PENDING_MARK, programme["id"]
    ) == [
        earlier,
        later,
    ]

    response = await cancel_occurrence(client, super_admin, programme["id"], later)

    assert response.status_code == 204, response.text
    for staff in ("regular_admin", "admin"):
        assert await _reminded(db_session, staff, PENDING_MARK, programme["id"]) == [
            earlier
        ]


@pytest.mark.requirement("notifications:R93a")
@pytest.mark.asyncio
async def test_should_resend_pending_mark_reminder_when_cancelled_session_restored(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    programme = await create_programme(client, super_admin, venue)
    earlier = at(days=-1, hours=-3)
    later = earlier + DAY_MS
    await _move_to(db_session, programme["id"], earlier)
    _ = await scan_pending_marks(db_session, now_ms())
    await db_session.commit()
    assert (
        await cancel_occurrence(client, super_admin, programme["id"], later)
    ).status_code == 204
    assert (
        await undo_cancel_occurrence(client, super_admin, programme["id"], later)
    ).status_code == 204

    _ = await scan_pending_marks(db_session, now_ms())
    await db_session.commit()

    assert await _reminded(
        db_session, "regular_admin", PENDING_MARK, programme["id"]
    ) == [
        earlier,
        later,
    ]


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_delete_reminders_from_cutoff_when_camp_series_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    camp = await create_camp(client, super_admin, venue, start=start, count=5)
    assert (await assign(client, admin, camp["id"], "skater")).status_code == 204
    days = [start + k * DAY_MS for k in range(5)]
    for slot in days:
        await _remind(db_session, "skater", UPCOMING, camp["id"], slot)

    response = await cancel_series(client, admin, camp["id"], days[2])

    assert response.status_code == 200, response.text
    assert await _reminded(db_session, "skater", UPCOMING, camp["id"]) == days[:2]


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_delete_reminders_from_cutoff_when_programme_terminated(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    days = [start + k * DAY_MS for k in range(4)]
    for slot in days:
        await _remind(db_session, "skater", UPCOMING, programme["id"], slot)

    response = await terminate(client, admin, programme["id"], days[2])

    assert response.status_code == 200, response.text
    assert await _reminded(db_session, "skater", UPCOMING, programme["id"]) == days[:2]


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_delete_every_reminder_when_event_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    other = await create_programme(
        client, super_admin, venue, start=start + 3 * HOUR_MS, title="Other"
    )
    for event_id in (programme["id"], other["id"]):
        assert (await assign(client, admin, event_id, "skater")).status_code == 204
        await _remind(db_session, "skater", UPCOMING, event_id, start)
    await _remind(db_session, "admin", PENDING_MARK, programme["id"], start)

    response = await client.delete(
        f"/v1/events/by_id/{programme['id']}", headers=auth(admin)
    )

    assert response.status_code in (200, 204), response.text
    assert await _reminded(db_session, "skater", UPCOMING, programme["id"]) == []
    assert await _reminded(db_session, "admin", PENDING_MARK, programme["id"]) == []
    assert await _reminded(db_session, "skater", UPCOMING, other["id"]) == [start]


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_delete_reminders_from_new_cutoff_when_extend_brings_it_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    days = [start + k * DAY_MS for k in range(4)]
    assert (await terminate(client, admin, programme["id"], days[3])).status_code == 200
    for slot in days[:3]:
        await _remind(db_session, "skater", UPCOMING, programme["id"], slot)

    response = await extend(client, admin, programme["id"], days[1])

    assert response.status_code == 200, response.text
    assert await _reminded(db_session, "skater", UPCOMING, programme["id"]) == [days[0]]


@pytest.mark.requirement("notifications:R87a")
@pytest.mark.asyncio
async def test_should_delete_upcoming_reminder_when_oneoff_dropped(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)
    assert (await assign(client, admin, oneoff["id"], "skater")).status_code == 204
    await _remind(db_session, "skater", UPCOMING, oneoff["id"], start)

    response = await drop(client, admin, oneoff["id"])

    assert response.status_code == 200, response.text
    assert await _reminded(db_session, "skater", UPCOMING, oneoff["id"]) == []
