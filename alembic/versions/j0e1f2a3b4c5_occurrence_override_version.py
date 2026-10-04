"""add version, updated_at and updated_by to occurrence_overrides (#430)

Revision ID: j0e1f2a3b4c5
Revises: dfb42e957e0d
Create Date: 2026-09-23 18:00:00.000000

Optimistic locking for single occurrences, on the terms events have had
since #292. An occurrence with no row is at version 1, so a row exists only
once the occurrence has changed at least once: existing rows start at 2.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, None] = "dfb42e957e0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "occurrence_overrides",
        sa.Column("version", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "occurrence_overrides", sa.Column("updated_at", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "occurrence_overrides",
        sa.Column("updated_by", sa.String(length=50), nullable=True),
    )
    op.create_foreign_key(
        "fk_occurrence_overrides_updated_by",
        "occurrence_overrides",
        "users",
        ["updated_by"],
        ["username"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_occurrence_overrides_updated_by",
        "occurrence_overrides",
        type_="foreignkey",
    )
    op.drop_column("occurrence_overrides", "updated_by")
    op.drop_column("occurrence_overrides", "updated_at")
    op.drop_column("occurrence_overrides", "version")
