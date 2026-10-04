"""Who may evaluate whom (#302, #535, R28-R33, R45).

The owner of an evaluation holds the coach role, in every scope (R45). Beyond
that, eligibility is scope-dependent. A general evaluation requires no
relationship at all, deliberately — a general impression is exactly the
document that does not presuppose one. An event evaluation requires both
halves: the coach coaches the event, the member has an attendance record
for it.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord
from ..db.models.event import Event
from ..db.models.user import Role, User
from ..exceptions import EvaluationNotEligibleException, EventNotFoundException
from ..schemas.common import UserRoles


class EvaluationEligibilityService:
    """Checks the owning coach and the member against the evaluation's scope."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def get_event_or_raise(self, event_id: int) -> Event:
        """Fetch a live event or raise."""
        result = await self.db.execute(
            select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
        )
        event = result.scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)
        return event

    async def _check_is_coach(self, coach_username: str) -> None:
        """An evaluation is owned by someone holding the coach role (R45)."""
        result = await self.db.execute(
            select(User).where(User.username == coach_username)
        )
        coach = result.scalar_one_or_none()
        roles = UserRoles.model_validate_json(coach.roles).roles if coach else []
        if Role.coach.value not in roles:
            raise EvaluationNotEligibleException(f"'{coach_username}' is not a coach")

    async def check(
        self,
        coach_username: str,
        member_username: str,
        event_id: int | None,
        period_start_utc: int | None = None,
        period_end_utc: int | None = None,
    ) -> None:
        """Raise unless this coach may evaluate this member in this scope."""
        await self._check_is_coach(coach_username)
        if event_id is None:
            return

        event = await self.get_event_or_raise(event_id)

        # Coach half. Coaches are recorded on the event, not the occurrence,
        # so "coached at least one session" is not answerable here (R32) and
        # the period cannot narrow this half at all (R33).
        if coach_username not in event.coach_names_list:
            raise EvaluationNotEligibleException(
                f"'{coach_username}' does not coach event {event.id}"
            )

        # Member half. Any attendance record counts whatever its status
        # (R31) — the test is that the member was tracked, not that they
        # turned up. Narrowed to the period when one is given (R33).
        query = select(AttendanceRecord.id).where(
            AttendanceRecord.event_id == event.id,
            AttendanceRecord.membername == member_username,
        )
        if period_start_utc is not None and period_end_utc is not None:
            query = query.where(
                AttendanceRecord.occurrence_time_utc >= period_start_utc,
                AttendanceRecord.occurrence_time_utc <= period_end_utc,
            )

        result = await self.db.execute(query.limit(1))
        if result.scalar_one_or_none() is None:
            where = "in that period" if period_start_utc else "for that event"
            raise EvaluationNotEligibleException(
                f"'{member_username}' has no attendance record {where}"
            )
