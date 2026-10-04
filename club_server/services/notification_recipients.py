"""Which users a notification may reach, by account state.

Every notification row is written through ``NotificationService`` or the
broadcast fan-out, and both ask this module which of the intended
recipients may receive it, so no domain service has to remember the rule.
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User, UserStatus


BLOCKED_DELIVERABLE_TYPES: frozenset[str] = frozenset(
    {
        "user.blocked",
        "user.unblocked",
        "user.deleted",
        "user.restored",
        "user.role_changed",
        "account.password_changed_by_admin",
        "account.password_changed_self",
        "profile.changed_by_admin",
        "credit.released",
        "evaluation.withdrawn",
    }
)
"""The notices about the account itself: the only types that reach a
blocked user. Club activity is suppressed for them (notifications R7b)."""


def deliverable_clause(notification_type: str) -> ColumnElement[bool]:
    """SQL condition on ``User`` for a recipient of ``notification_type``.

    A soft-deleted user and a user who has left receive nothing (#511); a
    blocked user receives only ``BLOCKED_DELIVERABLE_TYPES`` (#512).
    """
    clause = and_(
        User.deleted_at.is_(None),
        User.status != UserStatus.left.value,
    )
    if notification_type not in BLOCKED_DELIVERABLE_TYPES:
        clause = and_(clause, User.status != UserStatus.blocked.value)
    return clause


async def deliverable_usernames(
    db: AsyncSession, usernames: Iterable[str], notification_type: str
) -> set[str]:
    """The subset of ``usernames`` that may receive ``notification_type``.

    Unknown usernames are dropped along with undeliverable ones.
    """
    names = set(usernames)
    if not names:
        return set()
    result = await db.execute(
        select(User.username).where(
            User.username.in_(names), deliverable_clause(notification_type)
        )
    )
    return set(result.scalars().all())
