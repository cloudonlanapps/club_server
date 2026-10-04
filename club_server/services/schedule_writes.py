"""What every path that writes a schedule checks (#384).

Shared by creation, the programme split and the in-place reschedule of a
camp or a one-off, so that a window, a venue, an organizer, a timetable
and the scheduling horizon are validated the same way whichever verb writes
them.
"""

import json
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.user import User
from ..db.models.venue import Venue
from ..exceptions import (
    BeyondSchedulingHorizonException,
    InvalidDateTimeException,
    CoachNotFoundException,
    OrganizerNotFoundException,
    VenueNotFoundException,
)
from ..schemas.event import Session
from .event_eligibility import validate_sessions_against_window
from .event_types import is_bounded_type

if TYPE_CHECKING:
    from ..db.models.event_schedule import EventSchedule

CANCELLATION_LEAD_TIME_MS = 30 * 60 * 1000
"""The register opens 30 minutes before an occurrence (attendance R12), so
nothing may cancel, move or end a series inside that window."""

MAX_OCCURRENCE_MS = 24 * 60 * 60 * 1000
WEEK_MS = 7 * 24 * 60 * 60 * 1000


def validate_window(start_time: int, end_time: int) -> None:
    """An occurrence ends after it starts and lasts at most a day."""
    if end_time <= start_time:
        raise InvalidDateTimeException("End time must be after start time")
    if end_time - start_time > MAX_OCCURRENCE_MS:
        raise InvalidDateTimeException(
            "Session duration cannot exceed 24 hours. "
            "For multi-day events, use rrule to define recurrence."
        )


async def validate_venue(db: AsyncSession, venue_id: int) -> None:
    """The venue must exist and not be soft-deleted."""
    result = await db.execute(
        select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
    )
    if not result.scalar_one_or_none():
        raise VenueNotFoundException(venue_id)


async def validate_organizer(db: AsyncSession, organizer_name: str | None) -> None:
    """The organizer, when named, must be a live user (programme R20a)."""
    if not organizer_name:
        return
    result = await db.execute(
        select(User).where(User.username == organizer_name, User.deleted_at.is_(None))
    )
    if not result.scalar_one_or_none():
        raise OrganizerNotFoundException(organizer_name)


async def validate_coaches(db: AsyncSession, coach_names: list[str] | None) -> None:
    """Every named coach must be a live user (#386); the first missing one is named."""
    if not coach_names:
        return
    result = await db.execute(
        select(User.username).where(
            User.username.in_(coach_names), User.deleted_at.is_(None)
        )
    )
    found = set(result.scalars().all())
    for name in coach_names:
        if name not in found:
            raise CoachNotFoundException(name)


def validate_horizon(event_type: str, first_start_ms: int, now_ms: int) -> None:
    """A camp or one-off may not start beyond the scheduling horizon (one-off R20a)."""
    if not is_bounded_type(event_type):
        return
    weeks = settings.scheduling_horizon_weeks
    if first_start_ms > now_ms + weeks * WEEK_MS:
        raise BeyondSchedulingHorizonException(weeks)


def encode_sessions(sessions: list[Session] | None) -> str | None:
    """The timetable as stored: JSON, or NULL for none."""
    if not sessions:
        return None
    return json.dumps([s.model_dump() for s in sessions])


def decode_sessions(raw: str | None) -> list[Session] | None:
    """The stored timetable as ``Session`` objects, or ``None`` for none."""
    if not raw:
        return None
    return [Session(**s) for s in json.loads(raw)]


def validate_timetable(
    sessions: list[Session] | None, start_time: int, end_time: int
) -> None:
    """The timetable's periods must sum to the occurrence window (camp R101)."""
    validate_sessions_against_window(sessions, start_time, end_time)


def encode_names(names: list[str] | None) -> str | None:
    """A list of strings as stored: JSON, or NULL for none."""
    return json.dumps(names) if names else None


def schedule_fields_changed(
    schedule: "EventSchedule", **candidate: object
) -> dict[str, tuple[object, object]]:
    """Which of ``candidate``'s fields differ from ``schedule``'s current values."""
    changed: dict[str, tuple[object, object]] = {}
    for name, new in candidate.items():
        old = getattr(schedule, name)
        if new is not None and new != old:
            changed[name] = (old, new)
    return changed
