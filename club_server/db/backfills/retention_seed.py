"""Drop the retention row the table-creation migration seeded (#518).

Migration ``w7x8y9z0a1b2`` (#57) created ``system_preferences`` and inserted
``notification_info_retention_days`` = 90 with the migration time as
``updated_at`` and no writer. On every migrated database the key therefore
read as written, although nobody had written it, while a database built from
the models had no row and read the default with no update time (platform:R3a).

Removing the seeded row makes both agree: the key reads its server default,
90, with no update time or writer. Only a row no person wrote is removed —
the seeded value with no writer; a value someone set is kept.

Idempotent: a second run finds no such row and changes nothing.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

RETENTION_KEY = "notification_info_retention_days"
SEEDED_VALUE = 90


def drop_seeded_retention_row(conn: Connection) -> int:
    """Delete the seeded retention row if no person wrote it; return rows
    deleted."""
    result = conn.execute(
        text(
            """
            DELETE FROM system_preferences
            WHERE key = :key
              AND value = CAST(:value AS jsonb)
              AND updated_by IS NULL
            """
        ),
        {"key": RETENTION_KEY, "value": str(SEEDED_VALUE)},
    )
    return result.rowcount
