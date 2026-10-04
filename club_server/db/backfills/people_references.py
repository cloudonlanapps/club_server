"""Reconcile organizer and coach references before they become FKs (#386).

Until now ``event_schedules.organizer_name``, ``occurrence_overrides
.new_organizer_name`` and the JSON list in ``event_schedules.coach_names``
were unconstrained text, so rows naming a person who is not a user exist
precisely because nothing prevented them. The migration adds the FKs and
the ``event_schedule_coaches`` table; this runs first and makes the data
fit:

1. An organizer (on a schedule or an override) that names no user is
   cleared to NULL.
2. Every coach name that is a user becomes an ``event_schedule_coaches``
   row, keeping list order; names that are not users are dropped.

Soft-deleted users still exist and keep their references. Idempotent: a
second run finds no organizer to clear and every coach row already present.
"""

import json
import logging
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _clear_unknown(conn: Connection, table: str, column: str) -> int:
    result = conn.execute(
        text(
            f"UPDATE {table} SET {column} = NULL "
            f"WHERE {column} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM users u WHERE u.username = {table}.{column})"
        )
    )
    return result.rowcount


def _decode(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value: object = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(v) for v in cast(list[object], value)]


def reconcile_people_references(conn: Connection) -> dict[str, int]:
    """Clear unknown organizers and turn coach lists into rows; return counts."""
    counts = {
        "organizers_cleared": _clear_unknown(conn, "event_schedules", "organizer_name"),
        "override_organizers_cleared": _clear_unknown(
            conn, "occurrence_overrides", "new_organizer_name"
        ),
        "coaches_linked": 0,
        "coaches_dropped": 0,
    }
    known = set(conn.execute(text("SELECT username FROM users")).scalars().all())
    rows = conn.execute(
        text(
            "SELECT id, coach_names FROM event_schedules "
            "WHERE coach_names IS NOT NULL ORDER BY id"
        )
    ).all()
    for schedule_id, raw in rows:
        position = 0
        seen: set[str] = set()
        for name in _decode(raw):
            if name in seen:
                continue
            seen.add(name)
            if name not in known:
                counts["coaches_dropped"] += 1
                logger.warning(
                    "backfill[386]: schedule %s names coach %r who is not a user; dropped",
                    schedule_id,
                    name,
                )
                continue
            inserted = conn.execute(
                text(
                    "INSERT INTO event_schedule_coaches (schedule_id, username, position) "
                    "VALUES (:s, :u, :p) ON CONFLICT DO NOTHING"
                ),
                {"s": schedule_id, "u": name, "p": position},
            )
            counts["coaches_linked"] += inserted.rowcount
            position += 1
    return counts
