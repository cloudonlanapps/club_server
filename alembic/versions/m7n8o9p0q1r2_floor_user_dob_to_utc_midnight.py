"""dob fields: floor existing non-midnight rows to UTC midnight

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-05-13 00:00:00.000000

After #100, writes to ``users.date_of_birth``, ``groups.dob_on_or_after_utc``,
``groups.dob_on_or_before_utc``, ``events.dob_on_or_after_utc`` and
``events.dob_on_or_before_utc`` are rejected with 422 unless the value is at
00:00:00 UTC (ms divisible by 86_400_000). Older rows written before that
validation may still hold non-midnight values; this migration floors them so
reads and eligibility math see a consistent shape.

Floor (not round-nearest) is used here purely to match the column convention
the rest of the system expects. Any user-visible "wrong day" caused by a
historical timezone-buggy client is a pre-existing data issue and would need
the user to re-save their profile to correct.

Idempotent: re-running updates zero rows.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "m7n8o9p0q1r2"
down_revision: Union[str, None] = "l6m7n8o9p0q1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MS_PER_DAY = 24 * 60 * 60 * 1000


def _floor_column(table: str, column: str) -> None:
    op.execute(
        f"UPDATE {table} "
        f"SET {column} = {column} - ({column} % {MS_PER_DAY}) "
        f"WHERE {column} IS NOT NULL "
        f"AND {column} % {MS_PER_DAY} <> 0"
    )


def upgrade() -> None:
    _floor_column("users", "date_of_birth")
    _floor_column("groups", "dob_on_or_after_utc")
    _floor_column("groups", "dob_on_or_before_utc")
    _floor_column("events", "dob_on_or_after_utc")
    _floor_column("events", "dob_on_or_before_utc")


def downgrade() -> None:
    # Flooring is lossy; cannot restore the sub-day component.
    pass
