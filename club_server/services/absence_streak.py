"""The daily absence-streak scan (#66, #478).

Run by ``scheduler_loop`` in ``scheduler.py``. A member's streak is their
current run of absences; the warning is recorded per streak in
``absence_streak_warnings`` so it is sent once however long the streak runs
and whatever the notification retention sweep deletes.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.absence_streak_warning import AbsenceStreakWarning
from ..db.models.attendance import AttendanceRecord, AttendanceStatus
from .notification import NotificationEvent, NotificationService

ABSENCE_STREAK_THRESHOLD: int = 3


async def _current_streaks(session: AsyncSession) -> dict[str, tuple[int, int, int]]:
    """Each member's current run of absences, if it reaches the threshold.

    Returns ``{member: (length, first_absence_utc, last_absence_utc)}``. A
    member's streak is the run of absences at the end of their record: any
    other mark ends it (#478), so a streak that has since been broken is not
    reported however long it was.
    """
    records_result = await session.execute(
        select(
            AttendanceRecord.membername,
            AttendanceRecord.occurrence_time_utc,
            AttendanceRecord.status,
        ).order_by(
            AttendanceRecord.membername,
            AttendanceRecord.occurrence_time_utc,
        )
    )
    runs: dict[str, tuple[int, int, int]] = {}
    for member, occurrence, status in records_result.all():
        if status != AttendanceStatus.absent.value:
            _ = runs.pop(member, None)
            continue
        length, first, _last = runs.get(member, (0, occurrence, occurrence))
        runs[member] = (length + 1, first, occurrence)
    return {m: run for m, run in runs.items() if run[0] >= ABSENCE_STREAK_THRESHOLD}


async def scan_absence_streaks(session: AsyncSession, now: int) -> int:
    """Emit ``attendance.absence_streak_warning`` to users whose current
    run of consecutive absences reaches ``ABSENCE_STREAK_THRESHOLD``
    (currently 3).

    Fires once per streak (#478). A streak is identified by its member and
    its first absence, and the warning is recorded in
    ``absence_streak_warnings`` — not inferred from the notification, which
    the retention sweep deletes. A later mark that is not an absence ends
    the streak; the next run of absences is a new streak and warns again.
    """
    notifier = NotificationService(session)
    sent = 0
    for username, (length, first, last) in (await _current_streaks(session)).items():
        warned = await session.execute(
            select(AbsenceStreakWarning.id).where(
                AbsenceStreakWarning.membername == username,
                AbsenceStreakWarning.streak_start_utc == first,
            )
        )
        if warned.scalar_one_or_none() is not None:
            continue
        session.add(
            AbsenceStreakWarning(
                membername=username, streak_start_utc=first, warned_at=now
            )
        )
        emitted = await notifier.notify_for_event(
            NotificationEvent(
                type="attendance.absence_streak_warning",
                recipients=[username],
                data={
                    "streakLength": length,
                    "lastAbsenceTimeUtc": last,
                },
            )
        )
        sent += len(emitted)
    await session.flush()
    return sent
