"""add version and updated_by to events (#292)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-04 19:00:00.000000

Optimistic locking: every mutation bumps ``version`` and records who made
it in ``updated_by``, so an update carrying a stale version is refused and
the 409 can say who changed the event and when. Existing rows start at
version 1 with no recorded author.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "events", sa.Column("updated_by", sa.String(length=50), nullable=True)
    )
    op.create_foreign_key(
        "fk_events_updated_by",
        "events",
        "users",
        ["updated_by"],
        ["username"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_events_updated_by", "events", type_="foreignkey")
    op.drop_column("events", "updated_by")
    op.drop_column("events", "version")
