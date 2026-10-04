"""Event listings: admin, member, public, and the eligible-user roster.

Read-only companions to ``EventService``, kept apart so each file stays
readable. Range filters ask about the **current** schedule (the hybrid
properties on ``Event`` correlate to it), which is what a caller browsing
forward from today needs; an open-ended rule is always in range.
"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.enrollment import Enrollment
from ..db.models.event import Event
from ..db.models.user import User, UserStatus
from ..exceptions import EventNotFoundException
from ..schemas.common import PaginatedResponse
from ..schemas.event import EventResponse, EventScheduleResponse
from .event import NOTIFIABLE_ENROLLMENT_STATUSES
from .event_eligibility import (
    event_has_eligibility_criteria,
    is_user_eligible_for_event,
)
from .schedule import last_slot


def _open_ended_rule():
    return and_(Event.rrule.isnot(None), ~Event.rrule.contains("COUNT"))


class EventListingService:
    """Listings over events."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def list_events(
        self,
        from_time_utc: int | None = None,
        to_time_utc: int | None = None,
        event_type: str | None = None,
        visibility: str | None = None,
        venue_id: int | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[EventResponse]:
        """List events with filters (admin/coach)."""
        query = select(Event).where(Event.deleted_at.is_(None))
        if event_type:
            query = query.where(Event.type == event_type)
        if visibility:
            query = query.where(Event.visibility == visibility)
        if venue_id is not None:
            query = query.where(Event.venue_id == venue_id)
        if from_time_utc is not None:
            query = query.where(
                or_(
                    Event.start_time >= from_time_utc,
                    Event.until_time >= from_time_utc,
                    and_(Event.until_time.is_(None), _open_ended_rule()),
                )
            )
        if to_time_utc is not None:
            query = query.where(Event.start_time <= to_time_utc)

        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.offset(offset).limit(limit).order_by(Event.start_time)
        )
        return PaginatedResponse(
            items=[EventResponse.from_model(e) for e in result.scalars().all()],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_deleted_events(
        self, offset: int = 0, limit: int = 20
    ) -> PaginatedResponse[EventResponse]:
        """List soft-deleted events (admin only)."""
        query = select(Event).where(Event.deleted_at.isnot(None))
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            query.offset(offset).limit(limit).order_by(Event.deleted_at.desc())
        )
        return PaginatedResponse(
            items=[EventResponse.from_model(e) for e in result.scalars().all()],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_schedules(self, event_id: int) -> list[EventScheduleResponse]:
        """The event's schedules in timeline order (``event_schedule_model.md``)."""
        event = (
            await self.db.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none()
        if event is None:
            raise EventNotFoundException(event_id)
        return [EventScheduleResponse.from_model(s) for s in event.schedules]

    async def list_eligible_users(self, event_id: int) -> list[User]:
        """Users who can be assigned to or invited to this event.

        Excludes users with any non-terminal enrollment on the event, and
        applies the event's structured eligibility criteria when set.
        """
        event = (
            await self.db.execute(
                select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)

        enrolled_q = await self.db.execute(
            select(Enrollment.membername).where(
                Enrollment.event_id == event_id,
                Enrollment.status.in_(NOTIFIABLE_ENROLLMENT_STATUSES),
            )
        )
        enrolled = {row for (row,) in enrolled_q.all()}
        users_q = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
                User.is_super_admin == 0,
            )
        )
        has_criteria = event_has_eligibility_criteria(event)
        eligible = [
            u
            for u in users_q.scalars().all()
            if u.username not in enrolled
            and (not has_criteria or is_user_eligible_for_event(u, event))
        ]
        eligible.sort(key=lambda u: u.username)
        return eligible

    async def list_user_events(
        self,
        membername: str,
        from_time_utc: int | None = None,
        to_time_utc: int | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[EventResponse]:
        """Events the user can see in ``/myevents``.

        Any event the user has an enrollment on (active or terminal —
        grandfathering preserves history), plus public events the user is
        eligible for.
        """
        enrolled_active_event_ids = (
            select(Enrollment.event_id)
            .where(
                Enrollment.membername == membername,
                Enrollment.status.in_(NOTIFIABLE_ENROLLMENT_STATUSES),
            )
            .scalar_subquery()
        )
        any_enrollment_q = await self.db.execute(
            select(Enrollment.event_id).where(Enrollment.membername == membername)
        )
        any_enrollment_event_ids = {row for (row,) in any_enrollment_q.all()}
        user = (
            await self.db.execute(
                select(User).where(
                    User.username == membername, User.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()

        query = select(Event).where(
            Event.deleted_at.is_(None),
            or_(Event.id.in_(enrolled_active_event_ids), Event.visibility == "public"),
        )
        if from_time_utc is not None:
            query = query.where(
                or_(
                    Event.start_time >= from_time_utc,
                    Event.until_time >= from_time_utc,
                    and_(Event.until_time.is_(None), _open_ended_rule()),
                )
            )
        if to_time_utc is not None:
            query = query.where(Event.start_time <= to_time_utc)

        result = await self.db.execute(query.order_by(Event.start_time))
        events = list(result.scalars().all())
        if user is not None:
            events = [
                ev
                for ev in events
                if ev.id in any_enrollment_event_ids
                or is_user_eligible_for_event(user, ev)
            ]
        page = events[offset : offset + limit]
        return PaginatedResponse(
            items=[EventResponse.from_model(e) for e in page],
            total=len(events),
            offset=offset,
            limit=limit,
        )

    async def list_public_events(
        self,
        event_type: str | None = None,
        from_time_utc: int | None = None,
        to_time_utc: int | None = None,
        venue_id: int | None = None,
        organizer_name: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[EventResponse]:
        """List public events with filters. No authentication required."""
        query = select(Event).where(
            Event.visibility == "public", Event.deleted_at.is_(None)
        )
        if event_type:
            query = query.where(Event.type == event_type)
        if from_time_utc:
            # SQL fetches a superset; a bounded series still mid-run is kept
            # by the Python post-filter on its last occurrence (#151).
            query = query.where(
                and_(
                    or_(Event.until_time.is_(None), Event.until_time >= from_time_utc),
                    or_(
                        Event.start_time >= from_time_utc,
                        Event.end_time >= from_time_utc,
                        Event.rrule.isnot(None),
                    ),
                )
            )
        if to_time_utc:
            query = query.where(Event.start_time <= to_time_utc)
        if venue_id:
            query = query.where(Event.venue_id == venue_id)
        if organizer_name:
            query = query.where(Event.organizer_name == organizer_name)

        result = await self.db.execute(query.order_by(Event.start_time))
        events = list(result.scalars().all())
        if from_time_utc:
            events = [e for e in events if _still_running_at(e, from_time_utc)]
        return PaginatedResponse(
            items=[
                EventResponse.from_model(e) for e in events[offset : offset + limit]
            ],
            total=len(events),
            offset=offset,
            limit=limit,
        )

    async def get_public_event(self, event_id: int) -> Event:
        """Get a single public event by ID."""
        result = await self.db.execute(
            select(Event).where(
                Event.id == event_id,
                Event.visibility == "public",
                Event.deleted_at.is_(None),
            )
        )
        event = result.scalar_one_or_none()
        if not event:
            raise EventNotFoundException(event_id)
        return event


def _still_running_at(event: Event, from_time_utc: int) -> bool:
    """Whether the event has an occurrence ending at or after ``from_time_utc``."""
    if event.rrule is None:
        return True
    last = last_slot(event)
    if last is None:
        return True
    duration = event.end_time - event.start_time
    return last + duration >= from_time_utc
