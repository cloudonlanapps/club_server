"""Age bands, reference days and the window of birth dates (#16).

Pure arithmetic: no database, no HTTP. The rules are eligibility R4–R9 and
the club-day rule R3; the migration's inverse is R30.
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from club_server.age_eligibility import (
    Age,
    age_on,
    age_window,
    born_on,
    decode_age,
    encode_age,
    is_inverted_band,
)
from club_server.club_calendar import club_day, club_today, date_to_day
from club_server.config import Settings


def day(year: int, month: int, dom: int) -> int:
    return date_to_day(date(year, month, dom))


REFERENCE = day(2026, 6, 15)
FIVE = Age(years=5)
EIGHTEEN = Age(years=18)


@pytest.mark.requirement("eligibility:R5")
def test_should_admit_exact_ages_when_strict():
    window = age_window(FIVE, EIGHTEEN, True, REFERENCE)

    assert window.dob_on_or_after_utc == day(2008, 6, 15)
    assert window.dob_on_or_before_utc == day(2021, 6, 15)
    assert window.reference_day_utc == REFERENCE


@pytest.mark.requirement("eligibility:R6")
def test_should_widen_each_end_by_a_year_less_a_day_when_relaxed():
    window = age_window(FIVE, EIGHTEEN, False, REFERENCE)

    assert window.dob_on_or_after_utc == day(2007, 6, 16)
    assert window.dob_on_or_before_utc == day(2022, 6, 14)


@pytest.mark.requirement("eligibility:R7")
@pytest.mark.parametrize("strict", [True, False])
def test_should_place_no_limit_on_a_side_whose_bound_is_not_set(strict: bool):
    only_min = age_window(FIVE, None, strict, REFERENCE)
    only_max = age_window(None, EIGHTEEN, strict, REFERENCE)
    neither = age_window(None, None, strict, REFERENCE)

    assert only_min.dob_on_or_after_utc is None
    assert only_min.dob_on_or_before_utc is not None
    assert only_max.dob_on_or_after_utc is not None
    assert only_max.dob_on_or_before_utc is None
    assert (neither.dob_on_or_after_utc, neither.dob_on_or_before_utc) == (None, None)
    assert neither.admits(None) is True


@pytest.mark.requirement("eligibility:R8")
def test_should_admit_both_ends_of_the_window_and_refuse_the_days_outside():
    window = age_window(FIVE, EIGHTEEN, True, REFERENCE)

    assert window.admits(day(2008, 6, 15)) is True
    assert window.admits(day(2021, 6, 15)) is True
    assert window.admits(day(2008, 6, 14)) is False
    assert window.admits(day(2021, 6, 16)) is False


@pytest.mark.requirement("eligibility:R8")
def test_should_refuse_a_person_with_no_date_of_birth_when_a_bound_is_set():
    assert age_window(FIVE, None, True, REFERENCE).admits(None) is False
    assert age_window(None, EIGHTEEN, False, REFERENCE).admits(None) is False


@pytest.mark.requirement("eligibility:R9")
def test_should_take_months_and_days_off_after_years():
    age = Age(years=10, months=3, days=10)

    assert born_on(REFERENCE, age) == day(2016, 3, 5)


@pytest.mark.requirement("eligibility:R9")
def test_should_land_on_the_last_day_of_a_shorter_month():
    assert born_on(day(2024, 2, 29), Age(years=1)) == day(2023, 2, 28)
    assert born_on(day(2026, 3, 31), Age(years=0, months=1)) == day(2026, 2, 28)


@pytest.mark.requirement("eligibility:R2")
@pytest.mark.parametrize(
    "bad",
    [
        {"years": -1},
        {"years": 151},
        {"years": 5, "months": 12},
        {"years": 5, "days": 31},
        {"years": 5, "months": -1},
        {"months": 3},
        {"years": 5, "weeks": 2},
    ],
)
def test_should_refuse_an_age_outside_its_ranges(bad: dict[str, int]):
    with pytest.raises(ValidationError):
        _ = Age.model_validate(bad)


@pytest.mark.requirement("eligibility:R2")
def test_should_default_months_and_days_to_zero_and_round_trip():
    age = Age.model_validate({"years": 7})

    assert age.as_tuple() == (7, 0, 0)
    assert Age.model_validate(age.model_dump(by_alias=True)) == age
    assert decode_age(encode_age(age)) == age
    assert decode_age(None) is None
    assert encode_age(None) is None


@pytest.mark.requirement("eligibility:R10")
def test_should_call_a_band_inverted_only_when_minimum_exceeds_maximum():
    assert is_inverted_band(Age(years=6), Age(years=5, months=11, days=30)) is True
    assert is_inverted_band(Age(years=5), Age(years=5)) is False
    assert is_inverted_band(Age(years=5), None) is False
    assert is_inverted_band(None, Age(years=5)) is False


@pytest.mark.requirement("eligibility:R30")
def test_should_give_the_age_whose_strict_window_is_the_stored_date():
    """Every reference day against every birth date over four years, leap
    day and month ends included: the migration's age gives the date back."""
    start = date(2023, 12, 25)
    references = [start + timedelta(days=n) for n in range(0, 800, 7)]
    references += [date(2024, 2, 29), date(2025, 3, 31), date(2025, 5, 31)]
    for reference in references:
        reference_day = date_to_day(reference)
        for back in range(0, 1500):
            born = date_to_day(reference - timedelta(days=back))
            age = age_on(reference_day, born)
            assert born_on(reference_day, age) == born, (reference, back, age)


@pytest.mark.requirement("eligibility:R30")
def test_should_count_a_date_after_the_reference_day_as_age_zero():
    assert age_on(REFERENCE, day(2026, 6, 16)).as_tuple() == (0, 0, 0)
    assert age_on(REFERENCE, REFERENCE).as_tuple() == (0, 0, 0)


@pytest.mark.requirement("eligibility:R3")
def test_should_take_the_day_from_the_club_time_zone():
    """04:00 in India on 15 June is still 14 June in UTC."""
    instant = day(2026, 6, 14) + (22 * 60 + 30) * 60 * 1000

    assert club_day(instant, "Asia/Kolkata") == day(2026, 6, 15)
    assert club_day(instant, "UTC") == day(2026, 6, 14)
    assert club_today(instant, "Asia/Kolkata") == day(2026, 6, 15)


@pytest.mark.requirement("eligibility:R3")
def test_should_default_the_club_time_zone_to_india(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CLUB_TIMEZONE", raising=False)
    assert Settings().club_timezone == "Asia/Kolkata"

    monkeypatch.setenv("CLUB_TIMEZONE", "")
    assert Settings().club_timezone == "Asia/Kolkata"

    monkeypatch.setenv("CLUB_TIMEZONE", "Europe/Berlin")
    assert Settings().club_timezone == "Europe/Berlin"


@pytest.mark.requirement("eligibility:R3")
@pytest.mark.parametrize("bad", ["IST+5", "Asia/Nowhere", "5.5"])
def test_should_refuse_to_start_when_the_club_time_zone_is_unknown(
    monkeypatch: pytest.MonkeyPatch, bad: str
):
    monkeypatch.setenv("CLUB_TIMEZONE", bad)

    with pytest.raises(ValidationError) as failure:
        _ = Settings()

    assert "club_timezone" in str(failure.value)
