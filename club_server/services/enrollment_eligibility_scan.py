"""The daily scan for programme members who no longer match (#19).

Run by ``scheduler_loop`` in ``scheduler.py``. Eligibility is checked when a
member joins, and a programme's window moves with its next occurrence, so
an enrolled member can grow out of it. The scan tells the admins once;
nobody is removed. A camp's or a one-off's window is counted from a fixed
start day and does not move, so only programmes are scanned.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.enrollment import Enrollment
from ..db.models.event import Event
from .event import EventService
from .event_eligibility import ENROLLED_STATUSES, event_windows, still_meets_criteria
from .event_types import is_programme
from .lifecycle import has_live_occurrence_at_or_after
from .notification import NotificationEvent, NotificationService

MEMBER_INELIGIBLE = "enrollment.member_ineligible"


async def _running_programmes(session: AsyncSession, now: int) -> list[Event]:
    """Live programmes that still have an occurrence at or after ``now``."""
    events = (
        (await session.execute(select(Event).where(Event.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    return [
        event
        for event in events
        if is_programme(event)
        and await has_live_occurrence_at_or_after(session, event, now)
    ]


async def scan_enrollment_eligibility(session: AsyncSession, now: int) -> int:
    """Notify admins of enrolled programme members who have newly stopped matching.

    Each running programme's enrolled members are checked against its gender
    and the window its age band comes to on the day of its next live
    occurrence (eligibility R21, R24). The notice is recorded on the
    enrolment, so it goes out once however many scans run (R22); a member
    who matches again has the record cleared, and is reported afresh if
    they stop later (R23). Returns the notifications sent.
    """
    programmes = {e.id: e for e in await _running_programmes(session, now)}
    if not programmes:
        return 0
    windows = await event_windows(session, list(programmes.values()), now)
    enrollments = (
        (
            await session.execute(
                select(Enrollment).where(
                    Enrollment.event_id.in_(programmes),
                    Enrollment.status.in_(ENROLLED_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    notifier = NotificationService(session)
    admins: list[str] | None = None
    sent = 0
    for enrollment in enrollments:
        user = enrollment.user
        if user is None or user.deleted_at is not None:
            continue
        event = programmes[enrollment.event_id]
        if still_meets_criteria(enrollment, user, event, windows[event.id]):
            enrollment.ineligible_reported_at = None
            continue
        if enrollment.ineligible_reported_at is not None:
            continue
        enrollment.ineligible_reported_at = now
        if admins is None:
            admins = await EventService(session).list_admin_usernames()
        emitted = await notifier.notify_for_event(
            NotificationEvent(
                type=MEMBER_INELIGIBLE,
                recipients=admins,
                data={
                    "eventId": event.id,
                    "eventTitle": event.title,
                    "membername": enrollment.membername,
                },
            )
        )
        sent += len(emitted)
    await session.flush()
    return sent
