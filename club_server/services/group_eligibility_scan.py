"""The daily scan for semi-auto members who no longer match their group (#17).

Run by ``scheduler_loop`` in ``scheduler.py``. A semi-auto group stores its
members and checks them only when they join or its criteria are edited, and
an age band moves its window every day, so a member can grow out of it. The
scan tells the admins once; nobody is removed.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..club_calendar import club_today
from ..db.models.group import Group, GroupMember
from .group import KIND_SEMI_AUTO, GroupService, is_eligible_for_semi_auto
from .notification import NotificationEvent, NotificationService

MEMBER_INELIGIBLE = "group.member_ineligible"


async def scan_group_eligibility(session: AsyncSession, now: int) -> int:
    """Notify admins of semi-auto members who have newly stopped matching.

    Each live semi-auto group's stored members are checked against the
    window its age band comes to on the day ``now`` falls on, and against
    its gender; staff are exempt (groups R84, R87, R88). The notice is
    recorded on the membership row, so it goes out once however many scans
    run (R85); a member who matches again has the record cleared, and is
    reported afresh if they stop later (R86). Returns the notifications sent.
    """
    today = club_today(now)
    groups = (
        (
            await session.execute(
                select(Group)
                .where(Group.kind == KIND_SEMI_AUTO, Group.deleted_at.is_(None))
                .options(selectinload(Group.members).selectinload(GroupMember.user))
            )
        )
        .scalars()
        .all()
    )
    notifier = NotificationService(session)
    admins: list[str] | None = None
    sent = 0
    for group in groups:
        window = group.window_on(today)
        for member in group.members:
            if member.user is None or member.user.deleted_at is not None:
                continue
            if is_eligible_for_semi_auto(member.user, group, window):
                member.ineligible_reported_at = None
                continue
            if member.ineligible_reported_at is not None:
                continue
            member.ineligible_reported_at = now
            if admins is None:
                admins = await GroupService(session).admin_usernames()
            emitted = await notifier.notify_for_event(
                NotificationEvent(
                    type=MEMBER_INELIGIBLE,
                    recipients=admins,
                    data={
                        "groupId": group.id,
                        "groupName": group.name,
                        "membername": member.membername,
                    },
                )
            )
            sent += len(emitted)
    await session.flush()
    return sent
