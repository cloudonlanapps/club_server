"""Event eligibility: gender, and an age band counted on a reference day (#16).

An event's criteria are ``gender`` and the age band (``min_age``,
``max_age``, ``strict_age``) on the ``events`` table; an event with none
accepts any user. The band becomes a window of birth dates on the event's
reference day — the day a camp or one-off starts, a programme's next live
occurrence, today when it has none (eligibility R4, R4b) — and every check
and every response reads that one window.

Unlike the group helper, no staff exemption applies — eligibility is a
data-safety invariant, not a permission. Super-admins can adjust the
user's `date_of_birth`/`gender` or relax the event's criteria instead.

Sessions validation lives here too because it is enforced at the same
service-layer boundary as eligibility.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..age_eligibility import (
    Age,
    EligibilityWindow,
    age_window,
    decode_age,
    encode_age,
    is_inverted_band,
)
from ..club_calendar import club_day
from ..db.models.event import Event
from ..db.models.occurrence_override import OccurrenceOverride
from ..db.models.user import User
from ..exceptions import InvalidSessionsException, InvalidStateException
from ..schemas.common import ChangeLog
from ..schemas.event import EventResponse, Session
from ..utils import MS_PER_DAY, now_utc_ms
from .event_types import is_programme
from .lifecycle import CANCELLED_OVERRIDE_STATUS
from .schedule import event_slots

# How far ahead a programme is searched for its next live occurrence. A
# programme is weekly, so only a year of cancelled sessions exhausts it.
NEXT_OCCURRENCE_SEARCH_DAYS = 370

Overrides = dict[int, tuple[str, int | None]]
"""Slot → (override status, moved start) for one event."""


def event_has_eligibility_criteria(event: Event) -> bool:
    return any(v is not None for v in (event.gender, event.min_age, event.max_age))


def validate_age_band(min_age: Age | None, max_age: Age | None) -> None:
    """Reject a band whose minimum age is greater than its maximum (R10)."""
    if is_inverted_band(min_age, max_age):
        raise InvalidStateException("minAge must not be greater than maxAge")


def apply_age_band(
    event: Event,
    changes: ChangeLog,
    fields_set: set[str],
    min_age: Age | None,
    max_age: Age | None,
    strict_age: bool | None,
) -> None:
    """Write the fields of the age band a request named, then check it (R1, R10).

    A bound is written when the request carries it, null included, so null
    clears it and an omitted one keeps its value.
    """
    if "min_age" in fields_set:
        changes.add("min_age", event.min_age, encode_age(min_age))
        event.min_age = encode_age(min_age)
    if "max_age" in fields_set:
        changes.add("max_age", event.max_age, encode_age(max_age))
        event.max_age = encode_age(max_age)
    if strict_age is not None:
        changes.add("strict_age", bool(event.strict_age), strict_age)
        event.strict_age = strict_age
    validate_age_band(decode_age(event.min_age), decode_age(event.max_age))


def next_live_start(event: Event, overrides: Overrides, now_ms: int) -> int | None:
    """When the event's next occurrence that is not cancelled starts, if any.

    A postponed occurrence counts at the start it was moved to.
    """
    horizon = now_ms + NEXT_OCCURRENCE_SEARCH_DAYS * MS_PER_DAY
    cutoff = event.cutoff
    upper = horizon if cutoff is None else min(cutoff, horizon)
    for slot in event_slots(event, now_ms, upper):
        status, moved_start = overrides.get(slot.time, ("", None))
        if status == CANCELLED_OVERRIDE_STATUS:
            continue
        return moved_start if moved_start is not None else slot.time
    return None


def reference_day(
    event: Event, overrides: Overrides, now_ms: int, tz_name: str | None = None
) -> int:
    """The calendar day the event's ages are counted on (R4, R4b).

    tz_name is for the migration, which reads the club's time zone from
    the environment; everything else leaves it to the deploy setting.
    """
    if is_programme(event):
        upcoming = next_live_start(event, overrides, now_ms)
        return club_day(now_ms if upcoming is None else upcoming, tz_name)
    return club_day(event.first_schedule.start_time, tz_name)


def window_on(event: Event, reference_day_utc: int) -> EligibilityWindow:
    """The event's window of birth dates on a given reference day."""
    return age_window(
        decode_age(event.min_age),
        decode_age(event.max_age),
        bool(event.strict_age),
        reference_day_utc,
    )


async def _load_overrides(
    db: AsyncSession, event_ids: Sequence[int]
) -> dict[int, Overrides]:
    """The occurrence overrides of each event, for the reference-day search."""
    loaded: dict[int, Overrides] = {event_id: {} for event_id in event_ids}
    if not event_ids:
        return loaded
    rows = await db.execute(
        select(
            OccurrenceOverride.event_id,
            OccurrenceOverride.occurrence_time,
            OccurrenceOverride.status,
            OccurrenceOverride.new_start_time,
        ).where(OccurrenceOverride.event_id.in_(event_ids))
    )
    for event_id, slot, status, new_start in rows.tuples().all():
        loaded[event_id][slot] = (status, new_start)
    return loaded


async def event_windows(
    db: AsyncSession, events: Sequence[Event], now_ms: int | None = None
) -> dict[int, EligibilityWindow]:
    """Each event's window today, in one query for all their overrides.

    Only a programme's reference day depends on which occurrences are
    cancelled, so only programmes are looked up.
    """
    now = now_utc_ms() if now_ms is None else now_ms
    overrides = await _load_overrides(db, [e.id for e in events if is_programme(e)])
    return {
        e.id: window_on(e, reference_day(e, overrides.get(e.id, {}), now))
        for e in events
    }


async def event_window(
    db: AsyncSession, event: Event, now_ms: int | None = None
) -> EligibilityWindow:
    """The event's window of birth dates today."""
    return (await event_windows(db, [event], now_ms))[event.id]


def is_user_eligible_for_event(
    user: User, event: Event, window: EligibilityWindow
) -> bool:
    """Whether ``user`` meets the event's gender and falls inside its window.

    A user with no ``gender`` is ineligible for any event that sets one; a
    user with no ``date_of_birth`` is ineligible for any event with an age
    bound. Both ends of the window are inclusive (R8).
    """
    if event.gender is not None and (
        user.gender is None or user.gender != event.gender
    ):
        return False
    return window.admits(user.date_of_birth)


async def event_response(db: AsyncSession, event: Event) -> EventResponse:
    """An event as the API returns it, with its window worked out for today."""
    return EventResponse.from_model(event, await event_window(db, event))


async def event_responses(
    db: AsyncSession, events: Sequence[Event]
) -> list[EventResponse]:
    """``event_response`` for a page of events, with one override lookup."""
    windows = await event_windows(db, events)
    return [EventResponse.from_model(e, windows[e.id]) for e in events]


def validate_sessions_against_window(
    sessions: list[Session] | None,
    start_time_utc: int,
    end_time_utc: int,
) -> None:
    """Reject sessions whose periods don't sum to the per-occurrence window.

    NULL means "no timetable" and is always accepted. An empty list is
    rejected — clients should send NULL to clear.
    """
    if sessions is None:
        return
    if len(sessions) == 0:
        raise InvalidSessionsException(
            "sessions must be non-empty when provided; send null to clear",
            code="INVALID_SESSIONS_EMPTY",
        )
    actual = sum(s.period_minutes for s in sessions)
    duration_ms = end_time_utc - start_time_utc
    expected = duration_ms // 60_000
    if actual * 60_000 != duration_ms:
        raise InvalidSessionsException(
            f"sessions period total ({actual} min) must equal the per-occurrence "
            f"window ({expected} min)",
            expected_minutes=expected,
            actual_minutes=actual,
        )
