"""A schedule clash is reported, and the organizer decides (#72, programme R31d).

Every write that sets a schedule or its staff — creation, split, the
organizer or coach change, reinstate, the in-place reschedule and the move
of one occurrence — calls ``gate_schedule_write``. It runs the venue,
organizer and coach gates for every event type. A finding refuses the write
with the full report unless the request carries ``acceptConflicts``; an
accepted finding is returned for the admins' notice.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import EventConflictException
from ..schemas.event import ConflictReportResponse, EventConflictItemResponse
from .conflict_gates import (
    ConflictGate,
    Finding,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
)

SCHEDULE_GATES = {
    ConflictGate.venue: GatePolicy.advise,
    ConflictGate.organizer: GatePolicy.advise,
    ConflictGate.coach: GatePolicy.advise,
}
"""What every schedule or staff write asks, whatever the event type."""


def conflict_report(findings: list[Finding]) -> ConflictReportResponse:
    """The venue, organizer and coach findings, each with its occurrence pairs."""

    def items(gate: ConflictGate) -> list[EventConflictItemResponse]:
        return [
            EventConflictItemResponse.from_finding(f)
            for f in findings
            if f.gate is gate
        ]

    return ConflictReportResponse(
        venue_conflicts=items(ConflictGate.venue),
        organizer_conflicts=items(ConflictGate.organizer),
        coach_conflicts=items(ConflictGate.coach),
    )


async def gate_schedule_write(
    db: AsyncSession, target: ScheduleTarget, *, accept_conflicts: bool
) -> list[Finding]:
    """Run the three gates; refuse a clash the request has not accepted.

    Raises ``EventConflictException`` on any finding when
    ``accept_conflicts`` is false. Callers raise before they commit, and the
    session is rolled back with the request, so nothing is written. Returns
    the findings, empty when nothing clashes.
    """
    findings = (await check_conflicts(db, target, SCHEDULE_GATES)).findings
    if findings and not accept_conflicts:
        raise EventConflictException(findings)
    return findings
