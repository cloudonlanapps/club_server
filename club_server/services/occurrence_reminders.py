"""Delete the reminders of an occurrence that no longer needs them (#49, #50).

The scheduler sends ``event.upcoming_reminder`` to members and
``attendance.pending_mark_reminder`` to staff (``scheduler.py``). It never
sends one for a cancelled or marked occurrence, but a reminder already sent
stays in the inbox unless the change that made it pointless removes it.
These helpers do that, matching on the ``eventId`` and
``occurrenceTimeUtc`` the scan writes into the payload.

A reminder that becomes due again — a restored occurrence still inside a
lead time, a session whose marks were cleared — is sent again by the next
scan, since the row it would have found is gone.
"""

from typing import Any, cast

from sqlalchemy import BigInteger, ColumnElement, CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.notification import Notification

UPCOMING_REMINDER = "event.upcoming_reminder"
PENDING_MARK_REMINDER = "attendance.pending_mark_reminder"
OCCURRENCE_REMINDER_TYPES: tuple[str, ...] = (UPCOMING_REMINDER, PENDING_MARK_REMINDER)


def _data_field(name: str) -> ColumnElement[int]:
    """``payload.data.<name>`` as a BIGINT; occurrence times exceed INTEGER."""
    return Notification.payload[("data", name)].astext.cast(BigInteger)


async def delete_occurrence_reminders(
    db: AsyncSession,
    event_id: int,
    *,
    occurrence_time: int | None = None,
    from_time: int | None = None,
    types: tuple[str, ...] = OCCURRENCE_REMINDER_TYPES,
) -> int:
    """Delete ``types`` reminders of ``event_id``, for every recipient.

    ``occurrence_time`` narrows to one occurrence; ``from_time`` to the
    occurrences at or after it (a cutoff); neither means the whole event.
    Returns the number of rows deleted.
    """
    clauses = [
        Notification.type.in_(types),
        _data_field("eventId") == event_id,
    ]
    if occurrence_time is not None:
        clauses.append(_data_field("occurrenceTimeUtc") == occurrence_time)
    if from_time is not None:
        clauses.append(_data_field("occurrenceTimeUtc") >= from_time)
    result = cast(
        CursorResult[Any], await db.execute(delete(Notification).where(*clauses))
    )
    await db.flush()
    return result.rowcount
