"""Helpers for the age-eligibility tests (#16)."""

from datetime import date

from club_server.age_eligibility import Age, EligibilityWindow, age_on, age_window
from club_server.club_calendar import club_day, club_today, date_to_day
from club_server.utils import MS_PER_DAY

FIVE = {"years": 5, "months": 0, "days": 0}
EIGHTEEN = {"years": 18, "months": 0, "days": 0}


def day(year: int, month: int, dom: int) -> int:
    """A calendar date as UTC-midnight ms."""
    return date_to_day(date(year, month, dom))


def window_on(
    reference_day: int,
    min_age: dict | None = FIVE,
    max_age: dict | None = EIGHTEEN,
    *,
    strict: bool,
) -> EligibilityWindow:
    """The window the server is expected to report for a band on a day."""
    return age_window(
        None if min_age is None else Age.model_validate(min_age),
        None if max_age is None else Age.model_validate(max_age),
        strict,
        reference_day,
    )


def strict_band(
    reference_day: int,
    dob_on_or_after: int | None = None,
    dob_on_or_before: int | None = None,
) -> dict[str, object]:
    """The strict age band whose window on ``reference_day`` is these dates.

    Lets a test written around two fixed dates of birth keep them: the band
    it sends admits exactly the people the dates did.
    """
    band: dict[str, object] = {"strictAge": True}
    if dob_on_or_after is not None:
        band["maxAge"] = age_on(reference_day, dob_on_or_after).model_dump()
    if dob_on_or_before is not None:
        band["minAge"] = age_on(reference_day, dob_on_or_before).model_dump()
    return band


def group_band(
    dob_on_or_after: int | None = None, dob_on_or_before: int | None = None
) -> dict[str, object]:
    """``strict_band`` for a group, whose reference day is today."""
    return strict_band(club_today(), dob_on_or_after, dob_on_or_before)


def event_band(
    start_time_utc: int,
    dob_on_or_after: int | None = None,
    dob_on_or_before: int | None = None,
) -> dict[str, object]:
    """``strict_band`` for an event starting at ``start_time_utc``."""
    return strict_band(club_day(start_time_utc), dob_on_or_after, dob_on_or_before)


def edge_births(window: EligibilityWindow) -> dict[str, int]:
    """Dates of birth on and just outside each end of a two-sided window."""
    assert window.dob_on_or_after_utc is not None
    assert window.dob_on_or_before_utc is not None
    return {
        "too_old": window.dob_on_or_after_utc - MS_PER_DAY,
        "oldest": window.dob_on_or_after_utc,
        "youngest": window.dob_on_or_before_utc,
        "too_young": window.dob_on_or_before_utc + MS_PER_DAY,
    }


def _with_band(payload: dict, reference_day: int) -> dict:
    """``payload`` with its two date-of-birth bounds restated as a strict band.

    ``dobOnOrAfterUtc`` becomes ``maxAge`` and ``dobOnOrBeforeUtc`` becomes
    ``minAge``, each the age that gives the date back on ``reference_day``;
    a null stays a null, so it still clears the bound.
    """
    out = dict(payload)
    dated = False
    for old, new in (("dobOnOrAfterUtc", "maxAge"), ("dobOnOrBeforeUtc", "minAge")):
        if old not in out:
            continue
        value = out.pop(old)
        out[new] = None if value is None else age_on(reference_day, value).model_dump()
        dated = dated or value is not None
    if dated:
        out["strictAge"] = True
    return out


def with_group_band(payload: dict) -> dict:
    """A group payload written with date bounds, as the age band it means today."""
    return _with_band(payload, club_today())


def with_event_band(payload: dict, start_time_utc: int | None = None) -> dict:
    """An event payload written with date bounds, as the band it means on the
    event's start day (``startTimeUtc`` in the payload unless given)."""
    start = payload["startTimeUtc"] if start_time_utc is None else start_time_utc
    return _with_band(payload, club_day(start))
