"""create absence_streak_warnings (#478)

Revision ID: l2a3b4c5d6e7
Revises: k1f2a3b4c5d6
Create Date: 2026-09-26 12:00:00.000000

Records each absence streak a member has been warned about, keyed on the
member and the streak's first absence. The absence-streak scan dedupes on
this table instead of on its notification rows, which the retention sweep
deletes. Purely additive; no backfill — a streak already warned about under
the old rule is warned about once more, at most.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "l2a3b4c5d6e7"
down_revision: Union[str, None] = "k1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "absence_streak_warnings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "membername",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("streak_start_utc", sa.BigInteger(), nullable=False),
        sa.Column("warned_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("membername", "streak_start_utc"),
    )


def downgrade() -> None:
    op.drop_table("absence_streak_warnings")
