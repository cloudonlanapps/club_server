"""Attendance counts for the staff occurrence list (attendance R38a, #142).

Each listed occurrence gets ``attendance_expected``, the members whose
enrollment covers it (``enrollment_covers_occurrence``), and
``attendance_marked``, how many of them have a recorded status. A pending
leave request is not a recorded status. In a past session a member with a
recorded status also counts, as expected and as marked, when their
enrollment does not cover it: a super-admin may mark a member who enrolled
after the session, and the register shows that mark (#146). The whole list
costs two queries:
one for the enrollments of its events and one for their attendance records.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord, AttendanceStatus
from ..db.models.enrollment import Enrollment
from ..schemas.occurrence import OccurrenceResponse
from .eligibility import enrollment_covers_occurrence

MARKED_STATUSES = (
    AttendanceStatus.present.value,
    AttendanceStatus.absent.value,
    AttendanceStatus.late.value,
    AttendanceStatus.on_leave.value,
)


async def add_attendance_counts(
    db: AsyncSession, occurrences: list[OccurrenceResponse], now_ms: int
) -> None:
    """Set both counts on every occurrence in ``occurrences``, in place."""
    if not occurrences:
        return
    event_ids = {o.event_id for o in occurrences}
    times = [o.occurrence_time_utc for o in occurrences]

    enrollments_by_event: dict[int, list[Enrollment]] = {}
    for enrollment in (
        (await db.execute(select(Enrollment).where(Enrollment.event_id.in_(event_ids))))
        .scalars()
        .all()
    ):
        enrollments_by_event.setdefault(enrollment.event_id, []).append(enrollment)

    marked: dict[tuple[int, int], set[str]] = {}
    for row in (
        await db.execute(
            select(
                AttendanceRecord.event_id,
                AttendanceRecord.occurrence_time_utc,
                AttendanceRecord.membername,
            ).where(
                AttendanceRecord.event_id.in_(event_ids),
                AttendanceRecord.occurrence_time_utc >= min(times),
                AttendanceRecord.occurrence_time_utc <= max(times),
                AttendanceRecord.status.in_(MARKED_STATUSES),
            )
        )
    ).all():
        key = (row.event_id, row.occurrence_time_utc)
        marked.setdefault(key, set()).add(row.membername)

    for occurrence in occurrences:
        key = (occurrence.event_id, occurrence.occurrence_time_utc)
        expected = {
            e.membername
            for e in enrollments_by_event.get(occurrence.event_id, [])
            if enrollment_covers_occurrence(e, occurrence.occurrence_time_utc, now_ms)
        }
        session_marked = marked.get(key, set())
        if occurrence.occurrence_time_utc < now_ms:
            # A past session counts every member it has a mark for (#146).
            expected |= session_marked
        occurrence.attendance_expected = len(expected)
        occurrence.attendance_marked = len(expected & session_marked)
