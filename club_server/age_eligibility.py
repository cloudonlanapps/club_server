"""Age-based eligibility: from an age band to a window of birth dates (#16).

An event or a group stores a minimum and a maximum age and whether the
check is strict. Whenever eligibility is checked or reported, the band is
turned into a window of dates of birth, counted from a reference day: the
day a camp or one-off starts, a programme's next occurrence, today for a
group. Every date here is a calendar date carried as UTC-midnight
milliseconds, the way ``users.date_of_birth`` is.
"""

import json
from dataclasses import dataclass
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel, ConfigDict, Field

from .club_calendar import date_to_day, day_to_date
from .utils import MS_PER_DAY

MAX_AGE_YEARS = 150
MAX_AGE_MONTHS = 11
MAX_AGE_DAYS = 30

_ONE_YEAR = relativedelta(years=1)
_ONE_DAY = timedelta(days=1)


class Age(BaseModel):
    """A length of time counted the way a person's age is.

    Months stay below a year and days below a month, so two ages compare
    part by part and "17 months" is written as one year and five months.
    """

    model_config = ConfigDict(extra="forbid")

    years: int = Field(..., ge=0, le=MAX_AGE_YEARS)
    months: int = Field(0, ge=0, le=MAX_AGE_MONTHS)
    days: int = Field(0, ge=0, le=MAX_AGE_DAYS)

    def as_tuple(self) -> tuple[int, int, int]:
        """Years, months, days — the order two ages are compared in."""
        return (self.years, self.months, self.days)


@dataclass(frozen=True)
class EligibilityWindow:
    """The dates of birth an age band admits on one reference day.

    Both ends are inclusive; ``None`` places no limit on that side.
    """

    reference_day_utc: int
    dob_on_or_after_utc: int | None
    dob_on_or_before_utc: int | None

    def admits(self, date_of_birth: int | None) -> bool:
        """Whether a person born on ``date_of_birth`` is inside the window.

        A person with no date of birth is outside any window that has a
        limit. The upper end admits the whole of its day.
        """
        if self.dob_on_or_after_utc is None and self.dob_on_or_before_utc is None:
            return True
        if date_of_birth is None:
            return False
        if (
            self.dob_on_or_after_utc is not None
            and date_of_birth < self.dob_on_or_after_utc
        ):
            return False
        return not (
            self.dob_on_or_before_utc is not None
            and date_of_birth >= self.dob_on_or_before_utc + MS_PER_DAY
        )


def encode_age(age: Age | None) -> str | None:
    """An age as the JSON text its column stores, or ``None`` for no bound."""
    return None if age is None else json.dumps(age.model_dump())


def decode_age(raw: str | None) -> Age | None:
    """The age a column stores, or ``None`` when it stores no bound."""
    return None if raw is None else Age.model_validate(json.loads(raw))


def born_on(reference_day_utc: int, age: Age) -> int:
    """The date of birth of someone exactly ``age`` old on the reference day.

    Years and months are taken off first, landing on the last day of a
    shorter month (29 February less a year is 28 February); days are taken
    off last, so they are always exact.
    """
    day = day_to_date(reference_day_utc)
    day = day - relativedelta(years=age.years, months=age.months)
    return date_to_day(day - timedelta(days=age.days))


def age_on(reference_day_utc: int, date_of_birth_utc: int) -> Age:
    """The age for which ``born_on`` gives back exactly ``date_of_birth_utc``.

    The inverse the migration needs: a stored date bound becomes the strict
    age that admits the same people on the reference day. A date after the
    reference day has no age and counts as zero.
    """
    if date_of_birth_utc >= reference_day_utc:
        return Age(years=0)
    reference = day_to_date(reference_day_utc)
    born = day_to_date(date_of_birth_utc)
    whole = relativedelta(reference, born)
    years, months = whole.years, whole.months
    # Count whole years and months back from the reference day, then days;
    # a month too many (the clamp at a month's end) is given back.
    while reference - relativedelta(years=years, months=months) < born:
        years, months = (years, months - 1) if months else (years - 1, MAX_AGE_MONTHS)
    days = (reference - relativedelta(years=years, months=months) - born).days
    return Age(years=years, months=months, days=days)


def age_window(
    min_age: Age | None,
    max_age: Age | None,
    strict: bool,
    reference_day_utc: int,
) -> EligibilityWindow:
    """The window of birth dates an age band admits on the reference day.

    Strict admits those aged exactly ``min_age`` to exactly ``max_age`` on
    the day. Relaxed (the default) widens each end by a year less a day, so
    anyone who is ``min_age`` at some point in the coming year, or was
    ``max_age`` at some point in the last, is admitted.
    """
    after = before = None
    if max_age is not None:
        oldest = day_to_date(born_on(reference_day_utc, max_age))
        if not strict:
            oldest = oldest - _ONE_YEAR + _ONE_DAY
        after = date_to_day(oldest)
    if min_age is not None:
        youngest = day_to_date(born_on(reference_day_utc, min_age))
        if not strict:
            youngest = youngest + _ONE_YEAR - _ONE_DAY
        before = date_to_day(youngest)
    return EligibilityWindow(reference_day_utc, after, before)


def is_inverted_band(min_age: Age | None, max_age: Age | None) -> bool:
    """Whether the minimum age is greater than the maximum."""
    return (
        min_age is not None
        and max_age is not None
        and min_age.as_tuple() > max_age.as_tuple()
    )
