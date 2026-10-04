"""Regression test for #226.

A recurring event created with ``COUNT=N`` and later cancelled gets an
``until_time`` set while the ``COUNT`` stays in the rrule string. Expansion
must not build a dateutil rule carrying both COUNT and UNTIL (invalid per
RFC 5545; deprecated by dateutil and slated to raise). COUNT clipping is
preserved by the in-loop ``original_count`` check, so results are unchanged.
"""

import warnings
from datetime import datetime, timedelta, timezone

from club_server.services.rrule import generate_occurrences, get_next_occurrence

START = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def test_generate_occurrences_count_and_until_no_deprecation():
    until = START + timedelta(days=2) + timedelta(hours=1)  # clip after day 2 starts
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        occs = generate_occurrences(
            "FREQ=DAILY;COUNT=10", START, END, until_time_utc=until
        )
    # Days 0, 1, 2 fit; day 3 starts after until.
    assert occs == [START, START + timedelta(days=1), START + timedelta(days=2)]


def test_generate_occurrences_count_still_clips_when_until_is_later():
    until = START + timedelta(days=365)  # far past the COUNT bound
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        occs = generate_occurrences(
            "FREQ=DAILY;COUNT=3", START, END, until_time_utc=until
        )
    # COUNT=3 wins because it is the earlier bound.
    assert occs == [START, START + timedelta(days=1), START + timedelta(days=2)]


def test_get_next_occurrence_count_and_until_no_deprecation():
    until = START + timedelta(days=5)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        nxt = get_next_occurrence(
            "FREQ=DAILY;COUNT=10",
            START,
            START + timedelta(hours=2),
            until_time_utc=until,
        )
    assert nxt == START + timedelta(days=1)
