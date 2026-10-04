"""Absence-streak warnings: one per streak, and a present mark ends it (#478).

A member's streak is their current run of absences. A later present mark
ends it, so an ended streak is never warned about. The warning is deduped
on the streak itself, not on the notification row, so the retention sweep
deleting an old warning does not make the same streak warn again.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.attendance import AttendanceRecord, AttendanceStatus
from club_server.db.models.event import Event
from club_server.db.models.notification import Notification
from club_server.db.models.venue import Venue
from club_server.services.scheduler import scan_absence_streaks

from .helpers import create_admin_user, create_member_user

DAY_MS = 24 * 60 * 60 * 1000
WARNING = "attendance.absence_streak_warning"
A = AttendanceStatus.absent
P = AttendanceStatus.present


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp()) * 1000


async def _mark(
    db_session: AsyncSession, statuses: list[AttendanceStatus], first_day: int
) -> None:
    """Record one session per status for ``amy``, a day apart from ``first_day``."""
    venue = Venue(name="Streak Venue", created_at=0, updated_at=0)
    db_session.add(venue)
    await db_session.flush()
    for i, status in enumerate(statuses):
        occ = first_day + i * DAY_MS
        event = Event(
            title=f"E{occ}",
            type="programme",
            visibility="public",
            venue_id=venue.id,
            start_time=occ,
            end_time=occ + 60_000,
            created_at=0,
            updated_at=0,
        )
        db_session.add(event)
        await db_session.flush()
        db_session.add(
            AttendanceRecord(
                event_id=event.id,
                occurrence_time_utc=occ,
                membername="amy",
                status=status.value,
                recorded_at=occ,
            )
        )
    await db_session.commit()


async def _warnings(db_session: AsyncSession) -> list[Notification]:
    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.username == "amy", Notification.type == WARNING
        )
    )
    return list(result.scalars().all())


async def _setup(db_session: AsyncSession) -> int:
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy")
    return _now()


@pytest.mark.requirement("notifications:R96")
@pytest.mark.asyncio
async def test_should_not_warn_when_streak_was_ended_by_a_present_mark(
    db_session: AsyncSession,
):
    now = await _setup(db_session)
    await _mark(db_session, [A, A, A, P], now - 10 * DAY_MS)

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()

    assert sent == 0
    assert await _warnings(db_session) == []


@pytest.mark.requirement("notifications:R95")
@pytest.mark.asyncio
async def test_should_not_warn_again_when_old_warning_was_purged(
    db_session: AsyncSession,
):
    now = await _setup(db_session)
    await _mark(db_session, [A, A, A], now - 10 * DAY_MS)
    assert await scan_absence_streaks(db_session, now) == 1
    await db_session.commit()
    _ = await db_session.execute(
        delete(Notification).where(Notification.type == WARNING)
    )
    await db_session.commit()
    assert await _warnings(db_session) == []

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()

    assert sent == 0
    assert await _warnings(db_session) == []


@pytest.mark.requirement("notifications:R95")
@pytest.mark.asyncio
async def test_should_not_warn_again_when_the_same_streak_grows(
    db_session: AsyncSession,
):
    now = await _setup(db_session)
    await _mark(db_session, [A, A, A], now - 10 * DAY_MS)
    assert await scan_absence_streaks(db_session, now) == 1
    await db_session.commit()
    await _mark(db_session, [A], now - 7 * DAY_MS)

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()

    assert sent == 0
    assert len(await _warnings(db_session)) == 1


@pytest.mark.requirement("notifications:R96")
@pytest.mark.asyncio
async def test_should_warn_again_when_a_new_streak_follows_a_present_mark(
    db_session: AsyncSession,
):
    now = await _setup(db_session)
    await _mark(db_session, [A, A, A], now - 10 * DAY_MS)
    assert await scan_absence_streaks(db_session, now) == 1
    await db_session.commit()
    await _mark(db_session, [P, A, A, A], now - 7 * DAY_MS)

    sent = await scan_absence_streaks(db_session, now)
    await db_session.commit()

    assert sent == 1
    warnings = await _warnings(db_session)
    assert sorted(w.payload["data"]["lastAbsenceTimeUtc"] for w in warnings) == [
        now - 8 * DAY_MS,
        now - 4 * DAY_MS,
    ]
