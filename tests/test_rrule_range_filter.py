"""Pure-function tests for the [from, to) range filter in services/rrule.py.

Covers issue #289: the recurring branch of generate_occurrences applied the
``to_time_utc`` upper bound inclusively while the non-recurring branch applied
it exclusively, so an occurrence starting exactly on the bound was returned by
two adjacent day-range queries.
"""

from datetime import datetime, timedelta, timezone

from club_server.services.rrule import generate_occurrences

DAY = timedelta(days=1)
START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
DAILY = "FREQ=DAILY;COUNT=5"


def test_should_exclude_recurring_occurrence_when_it_starts_exactly_on_to_time():
    """A recurrence landing on the upper bound is outside a half-open range."""
    occurrences = generate_occurrences(
        rrule_string=DAILY,
        start_time_utc=START,
        end_time_utc=END,
        from_time_utc=START,
        to_time_utc=START + 2 * DAY,
    )
    assert occurrences == [START, START + DAY]


def test_should_exclude_non_recurring_occurrence_when_it_starts_exactly_on_to_time():
    """The non-recurring branch is already half-open; pin it so both agree."""
    occurrences = generate_occurrences(
        rrule_string="",
        start_time_utc=START,
        end_time_utc=END,
        from_time_utc=START - DAY,
        to_time_utc=START,
    )
    assert occurrences == []


def test_should_include_recurring_occurrence_when_it_starts_just_before_to_time():
    """Guard against over-correcting the bound into the previous instant."""
    occurrences = generate_occurrences(
        rrule_string=DAILY,
        start_time_utc=START,
        end_time_utc=END,
        from_time_utc=START,
        to_time_utc=START + 2 * DAY - timedelta(milliseconds=1),
    )
    assert occurrences == [START, START + DAY]


def test_should_return_boundary_occurrence_in_exactly_one_of_two_adjacent_ranges():
    """Adjacent day queries must not both report the same occurrence."""
    boundary = START + 2 * DAY
    previous_day = generate_occurrences(
        rrule_string=DAILY,
        start_time_utc=START,
        end_time_utc=END,
        from_time_utc=boundary - DAY,
        to_time_utc=boundary,
    )
    current_day = generate_occurrences(
        rrule_string=DAILY,
        start_time_utc=START,
        end_time_utc=END,
        from_time_utc=boundary,
        to_time_utc=boundary + DAY,
    )
    assert boundary not in previous_day
    assert boundary in current_day
    assert previous_day == [boundary - DAY]
    assert current_day == [boundary]
