"""age-based eligibility on events and groups (#16)

Revision ID: 278700baeb87
Revises: q7f8a9b0c1d2
Create Date: 2026-10-06

Events and groups stored eligibility as two fixed dates of birth. They now
store an age band — ``min_age``, ``max_age`` (JSON years/months/days) and
``strict_age`` — and the dates are worked out from it on a reference day.

The upgrade adds the band columns, converts every stored date bound to the
strict age that gives the same date back today (the body lives in
``club_server.db.backfills.age_eligibility`` so it is tested against the
per-function test DB), then drops the date columns. The club's time zone is
read from ``CLUB_TIMEZONE``; a programme that carries a bound needs the
server's settings in the environment, because its schedule is expanded to
find its next occurrence.

The downgrade puts the date columns back holding the window each band comes
to on the day it runs.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from club_server.db.backfills.age_eligibility import (
    convert_dob_bounds_to_age_bands,
    restore_dob_bounds_from_age_bands,
)


# revision identifiers, used by Alembic.
revision: str = "278700baeb87"
down_revision: Union[str, Sequence[str], None] = "q7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("events", "groups")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("min_age", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("max_age", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "strict_age",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    _ = convert_dob_bounds_to_age_bands(op.get_bind())
    for table in TABLES:
        op.drop_column(table, "dob_on_or_after_utc")
        op.drop_column(table, "dob_on_or_before_utc")


def downgrade() -> None:
    for table in TABLES:
        op.add_column(
            table, sa.Column("dob_on_or_after_utc", sa.BigInteger(), nullable=True)
        )
        op.add_column(
            table, sa.Column("dob_on_or_before_utc", sa.BigInteger(), nullable=True)
        )
    _ = restore_dob_bounds_from_age_bands(op.get_bind())
    for table in TABLES:
        op.drop_column(table, "strict_age")
        op.drop_column(table, "max_age")
        op.drop_column(table, "min_age")
