"""Turn stored date-of-birth bounds into age bands (#16, eligibility R30).

Events and groups used to store two fixed dates of birth. Each bound
becomes the strict age that, counted from the record's reference day on the
day this runs, gives the same date back — so every record admits the same
people immediately afterwards, and programmes and groups move forward from
then. The reference day is the one the server reports: today for a group,
the start day for a camp or one-off, the next live occurrence for a
programme (today when it has none).

Runs between adding the age columns and dropping the date columns. The
club's time zone is read from ``CLUB_TIMEZONE`` rather than ``Settings`` so
alembic needs no more of the environment than it must. A programme that
carries a bound needs its schedule expanded, which loads the services
package and with it the server's settings; nothing else does.
"""

import logging
import os
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...age_eligibility import age_on, age_window, decode_age, encode_age
from ...club_calendar import DEFAULT_CLUB_TIMEZONE, club_day, club_today
from ...utils import now_utc_ms

logger = logging.getLogger(__name__)

PROGRAMME_TYPE = "programme"


def club_timezone_from_env() -> str:
    """The club's time zone as the deployment names it, or the default."""
    return os.environ.get("CLUB_TIMEZONE", "").strip() or DEFAULT_CLUB_TIMEZONE


def _band(
    reference_day: int, dob_on_or_after: int | None, dob_on_or_before: int | None
) -> dict[str, str | None]:
    """The strict band whose window on ``reference_day`` is the two dates.

    Born on or after a date means at most that old, so the earlier date is
    the maximum age and the later one the minimum.
    """
    return {
        "max_age": None
        if dob_on_or_after is None
        else encode_age(age_on(reference_day, dob_on_or_after)),
        "min_age": None
        if dob_on_or_before is None
        else encode_age(age_on(reference_day, dob_on_or_before)),
    }


def _write(conn: Connection, table: str, row_id: int, band: dict[str, str | None]):
    _ = conn.execute(
        text(
            f"UPDATE {table} SET min_age = :min_age, max_age = :max_age, "
            "strict_age = true WHERE id = :id"
        ),
        {**band, "id": row_id},
    )


def _convert_groups(conn: Connection, today: int) -> int:
    rows = conn.execute(
        text(
            "SELECT id, dob_on_or_after_utc, dob_on_or_before_utc FROM groups "
            "WHERE dob_on_or_after_utc IS NOT NULL OR dob_on_or_before_utc IS NOT NULL"
        )
    ).all()
    for group_id, after, before in rows:
        _write(conn, "groups", group_id, _band(today, after, before))
    return len(rows)


def _programme_reference_day(
    conn: Connection,
    event_id: int,
    schedules: list[SimpleNamespace],
    now_ms: int,
    tz: str,
) -> int:
    """A programme's reference day, by the function the server itself uses."""
    from ...services.event_eligibility import reference_day

    overrides = {
        slot: (status, new_start)
        for slot, status, new_start in conn.execute(
            text(
                "SELECT occurrence_time, status, new_start_time "
                "FROM occurrence_overrides WHERE event_id = :id"
            ),
            {"id": event_id},
        ).all()
    }
    event = SimpleNamespace(
        id=event_id,
        type=PROGRAMME_TYPE,
        schedules=schedules,
        first_schedule=schedules[0],
        current_schedule=schedules[-1],
        cutoff=schedules[-1].effective_until,
    )
    return reference_day(event, overrides, now_ms, tz)  # pyright: ignore[reportArgumentType]


def _event_reference_day(
    conn: Connection, event_id: int, event_type: str, now_ms: int, tz: str
) -> int:
    """The reference day the server reports for this event right now."""
    schedules = [
        SimpleNamespace(
            effective_from=effective_from,
            effective_until=effective_until,
            start_time=start_time,
            end_time=end_time,
            rrule=rrule,
        )
        for effective_from, effective_until, start_time, end_time, rrule in conn.execute(
            text(
                "SELECT effective_from, effective_until, start_time, end_time, rrule "
                "FROM event_schedules WHERE event_id = :id ORDER BY effective_from"
            ),
            {"id": event_id},
        ).all()
    ]
    if not schedules:
        return club_today(now_ms, tz)
    if event_type == PROGRAMME_TYPE:
        return _programme_reference_day(conn, event_id, schedules, now_ms, tz)
    return club_day(schedules[0].start_time, tz)


def _convert_events(conn: Connection, now_ms: int, tz: str) -> int:
    rows = conn.execute(
        text(
            "SELECT id, type, dob_on_or_after_utc, dob_on_or_before_utc FROM events "
            "WHERE dob_on_or_after_utc IS NOT NULL OR dob_on_or_before_utc IS NOT NULL"
        )
    ).all()
    for event_id, event_type, after, before in rows:
        reference = _event_reference_day(conn, event_id, event_type, now_ms, tz)
        _write(conn, "events", event_id, _band(reference, after, before))
    return len(rows)


def convert_dob_bounds_to_age_bands(
    conn: Connection, now_ms: int | None = None, tz_name: str | None = None
) -> dict[str, int]:
    """Give every event and group with a date bound its strict age band."""
    now = now_utc_ms() if now_ms is None else now_ms
    tz = tz_name or club_timezone_from_env()
    counts = {
        "groups": _convert_groups(conn, club_today(now, tz)),
        "events": _convert_events(conn, now, tz),
    }
    logger.info("age bands written: %s", counts)
    return counts


def restore_dob_bounds_from_age_bands(
    conn: Connection, now_ms: int | None = None, tz_name: str | None = None
) -> dict[str, int]:
    """The downgrade: write each band back as the two dates it comes to today."""
    now = now_utc_ms() if now_ms is None else now_ms
    tz = tz_name or club_timezone_from_env()
    counts = {"groups": 0, "events": 0}
    for table in counts:
        columns = "id, min_age, max_age, strict_age" + (
            ", type" if table == "events" else ""
        )
        rows = conn.execute(
            text(
                f"SELECT {columns} FROM {table} "
                "WHERE min_age IS NOT NULL OR max_age IS NOT NULL"
            )
        ).all()
        for row in rows:
            reference = (
                _event_reference_day(conn, row[0], row[4], now, tz)
                if table == "events"
                else club_today(now, tz)
            )
            window = age_window(
                decode_age(row[1]), decode_age(row[2]), bool(row[3]), reference
            )
            _ = conn.execute(
                text(
                    f"UPDATE {table} SET dob_on_or_after_utc = :after, "
                    "dob_on_or_before_utc = :before WHERE id = :id"
                ),
                {
                    "after": window.dob_on_or_after_utc,
                    "before": window.dob_on_or_before_utc,
                    "id": row[0],
                },
            )
        counts[table] = len(rows)
    return counts
