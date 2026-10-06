"""The club's calendar: which day an instant falls on, where the club is (#16).

Ages are counted on calendar days — today, or the day an event starts — and
a day is the club's own, not UTC's: a session at 05:00 in India is still on
the previous UTC day. A calendar date is carried the way dates of birth
are, as the UTC-midnight millisecond timestamp of that date, so a day
worked out here compares directly with ``users.date_of_birth``.

The time zone is the ``CLUB_TIMEZONE`` deploy setting. Nothing here imports
``config`` at module level, so a migration can pass the zone it read from
the environment instead of constructing ``Settings``.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .utils import now_utc_ms

DEFAULT_CLUB_TIMEZONE = "Asia/Kolkata"


def _zone(tz_name: str | None) -> ZoneInfo:
    if tz_name is None:
        from .config import settings

        tz_name = settings.club_timezone
    return ZoneInfo(tz_name)


def date_to_day(day: date) -> int:
    """A calendar date as its UTC-midnight millisecond timestamp."""
    return int(
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000
    )


def day_to_date(day_ms: int) -> date:
    """The calendar date a UTC-midnight millisecond timestamp stands for."""
    return datetime.fromtimestamp(day_ms / 1000, tz=timezone.utc).date()


def club_day(instant_ms: int, tz_name: str | None = None) -> int:
    """The club's calendar day ``instant_ms`` falls on, as UTC-midnight ms."""
    local = datetime.fromtimestamp(instant_ms / 1000, tz=_zone(tz_name))
    return date_to_day(local.date())


def club_today(now_ms: int | None = None, tz_name: str | None = None) -> int:
    """Today on the club's calendar, as UTC-midnight ms."""
    return club_day(now_utc_ms() if now_ms is None else now_ms, tz_name)
