"""add version and updated_by to event_marketing (#13)

Revision ID: c4e7a91b2d13
Revises: a1f14a4c4be5
Create Date: 2026-10-08

Optimistic locking for an event's marketing block, on the terms occurrences
have had since #430. An event with no row is at version 1, so a row exists
only once the block has been written at least once: existing rows start at
2. The row already carries ``updated_at``; who wrote it last was not
recorded, so ``updated_by`` starts NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4e7a91b2d13"
down_revision: Union[str, Sequence[str], None] = "a1f14a4c4be5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "event_marketing",
        sa.Column("version", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "event_marketing",
        sa.Column("updated_by", sa.String(length=50), nullable=True),
    )
    op.create_foreign_key(
        "fk_event_marketing_updated_by",
        "event_marketing",
        "users",
        ["updated_by"],
        ["username"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_event_marketing_updated_by", "event_marketing", type_="foreignkey"
    )
    op.drop_column("event_marketing", "updated_by")
    op.drop_column("event_marketing", "version")
