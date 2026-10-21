"""Marking a session removes its pending-mark reminder from every staff inbox (#50).

``docs/notifications_requirements.md`` R93b. The hourly scan sends
``attendance.pending_mark_reminder`` to every admin and coach for a session
that ended unmarked (R92); one coach marking it must clear the reminder for
all of them, and leave the reminders of other sessions alone.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event import Event
from club_server.db.models.notification import Notification
from club_server.services.scheduler import scan_pending_marks

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    assign,
    at,
    backdate_enrollment,
    create_programme,
    create_venue,
    mark,
    now_ms,
)

PENDING_MARK = "attendance.pending_mark_reminder"
STAFF = ("admin", "regular_admin", "coach")


async def _reminded(
    db_session: AsyncSession, username: str, event_id: int
) -> list[int]:
    """Occurrence times ``username`` holds a pending-mark reminder for."""
    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Notification.payload).where(
                Notification.username == username, Notification.type == PENDING_MARK
            )
        )
    ).all()
    return sorted(
        p["data"]["occurrenceTimeUtc"]
        for (p,) in rows
        if p["data"]["eventId"] == event_id
    )


async def _two_unmarked_sessions(
    client: AsyncClient, db_session: AsyncSession, super_admin: str, admin: str
) -> tuple[int, int, int]:
    """A daily programme with ``skater`` enrolled whose last two sessions
    ended unmarked, reminded to staff by the scan."""
    venue = await create_venue(client, super_admin)
    programme = await create_programme(client, super_admin, venue)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    earlier = at(days=-1, hours=-3)
    event = (
        await db_session.execute(select(Event).where(Event.id == programme["id"]))
    ).scalar_one()
    event.start_time = earlier
    event.end_time = earlier + HOUR_MS
    await db_session.commit()
    await backdate_enrollment(db_session, programme["id"], "skater", earlier - HOUR_MS)
    assert await scan_pending_marks(db_session, now_ms()) > 0
    await db_session.commit()
    return programme["id"], earlier, earlier + DAY_MS


@pytest.mark.requirement("notifications:R93b")
@pytest.mark.asyncio
async def test_should_delete_pending_mark_reminder_for_every_staff_when_session_marked(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    coach = await create_coach_user(db_session)
    _ = await create_member_user(db_session, "skater")
    event_id, earlier, later = await _two_unmarked_sessions(
        client, db_session, super_admin, admin
    )
    for staff in STAFF:
        assert await _reminded(db_session, staff, event_id) == [earlier, later]
    _ = coach

    response = await mark(client, admin, event_id, later, "skater")

    assert response.status_code == 200, response.text
    for staff in STAFF:
        assert await _reminded(db_session, staff, event_id) == [earlier]


@pytest.mark.requirement("notifications:R93b")
@pytest.mark.asyncio
async def test_should_not_resend_pending_mark_reminder_when_session_marked(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    event_id, earlier, later = await _two_unmarked_sessions(
        client, db_session, super_admin, admin
    )
    assert (await mark(client, admin, event_id, later, "skater")).status_code == 200

    _ = await scan_pending_marks(db_session, now_ms())
    await db_session.commit()

    assert await _reminded(db_session, "regular_admin", event_id) == [earlier]
