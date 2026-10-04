"""Event eligibility helper.

Mirrors the group-eligibility convention introduced in #11. An event's
structured eligibility criteria are `gender`, `dob_on_or_after_utc`, and
`dob_on_or_before_utc` columns directly on the `events` table. Events
with all three NULL accept any user.

Unlike the group helper, no staff exemption applies — eligibility is a
data-safety invariant, not a permission. Super-admins can adjust the
user's `date_of_birth`/`gender` or relax the event's criteria instead.

Sessions validation lives here too because it is enforced at the same
service-layer boundary as eligibility.
"""

from ..db.models.event import Event
from ..db.models.user import User
from ..exceptions import InvalidSessionsException, InvalidStateException
from ..schemas.event import Session
from ..utils import MS_PER_DAY


def event_has_eligibility_criteria(event: Event) -> bool:
    return any(
        v is not None
        for v in (event.gender, event.dob_on_or_after_utc, event.dob_on_or_before_utc)
    )


def is_user_eligible_for_event(user: User, event: Event) -> bool:
    """Pure-Python eligibility check for an event's DOB window + gender.

    DOB bounds are stored as UTC midnight; both endpoints are inclusive at
    the day level (the upper-bound day is fully included, so the exclusion
    threshold is `dob_on_or_before_utc + 1 day`).

    A user with NULL `gender` is ineligible for any event that has
    `gender` set; a user with NULL `date_of_birth` is ineligible for any
    event that has either DOB bound set.
    """
    if event.gender is not None:
        if user.gender is None or user.gender != event.gender:
            return False
    if event.dob_on_or_after_utc is not None or event.dob_on_or_before_utc is not None:
        if user.date_of_birth is None:
            return False
        if (
            event.dob_on_or_after_utc is not None
            and user.date_of_birth < event.dob_on_or_after_utc
        ):
            return False
        if (
            event.dob_on_or_before_utc is not None
            and user.date_of_birth >= event.dob_on_or_before_utc + MS_PER_DAY
        ):
            return False
    return True


def validate_dob_window(
    dob_on_or_after_utc: int | None,
    dob_on_or_before_utc: int | None,
) -> None:
    """Reject an inverted DOB window. Mirrors the rule applied to groups."""
    if (
        dob_on_or_after_utc is not None
        and dob_on_or_before_utc is not None
        and dob_on_or_after_utc > dob_on_or_before_utc
    ):
        raise InvalidStateException(
            "dobOnOrAfterUtc must not be later than dobOnOrBeforeUtc"
        )


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
