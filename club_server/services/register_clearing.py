"""Clear the register of occurrences that will not happen (#337; attendance R21b–R21d).

A cancelled session keeps no rows saying who was there. Only the marks
staff make — ``present`` / ``absent`` / ``late`` — are deleted; leave rows
belong to the member and their approval flow and are kept.

Credit is refunded **before** the rows go: the refund discovers whom to
repay by reading the attendance rows of the occurrence (credit R48), so
deleting first would leave every member silently charged.
"""

from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord, AttendanceStatus
from ..db.models.event import Event
from .credit_charge import CreditChargeService

CLEARED_STATUSES = frozenset(
    {
        AttendanceStatus.present.value,
        AttendanceStatus.absent.value,
        AttendanceStatus.late.value,
    }
)


async def marked_occurrences_at_or_after(
    db: AsyncSession, event_id: int, cutoff_ms: int
) -> list[int]:
    """Occurrence times of ``event_id`` carrying a staff mark at or after the cutoff."""
    result = await db.execute(
        select(AttendanceRecord.occurrence_time_utc)
        .where(
            AttendanceRecord.event_id == event_id,
            AttendanceRecord.occurrence_time_utc >= cutoff_ms,
            AttendanceRecord.status.in_(CLEARED_STATUSES),
        )
        .distinct()
        .order_by(AttendanceRecord.occurrence_time_utc)
    )
    return list(result.scalars().all())


async def clear_cancelled_registers(
    db: AsyncSession,
    event: Event,
    occurrence_times: Iterable[int],
    actor: str | None,
) -> int:
    """Refund, then delete, the staff marks of occurrences that are now cancelled.

    Call once the occurrences read as cancelled (override written, or cutoff
    set): the refund is the ordinary reconciliation, which charges nothing for
    a cancelled occurrence (R48). Returns the number of rows deleted.
    """
    credit = CreditChargeService(db)
    deleted = 0
    for occurrence_time in occurrence_times:
        await credit.reconcile_occurrence(event.id, occurrence_time, actor)
        result = cast(
            CursorResult[Any],
            await db.execute(
                delete(AttendanceRecord).where(
                    AttendanceRecord.event_id == event.id,
                    AttendanceRecord.occurrence_time_utc == occurrence_time,
                    AttendanceRecord.status.in_(CLEARED_STATUSES),
                )
            ),
        )
        deleted += result.rowcount
    await db.flush()
    return deleted
