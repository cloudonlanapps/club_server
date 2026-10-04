"""notifications: add pending_action_key text column

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-05-12 00:00:00.000000

Adds a nullable text column ``pending_action_key`` to ``notifications``
so that pending-action pointers can target rows whose primary key is a
string (e.g. ``users.username``) rather than an integer. Existing
pending-action types continue to use the integer ``pending_action_id``;
the new ``user_approval`` type uses ``pending_action_key``.

Both columns are independent — a notification may set either or neither,
and the join function registered for a given ``pending_action_type``
decides which column to consult.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l6m7n8o9p0q1"
down_revision: Union[str, None] = "k5l6m7n8o9p0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("pending_action_key", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_notifications_pending_action_key",
        "notifications",
        ["pending_action_type", "pending_action_key"],
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_pending_action_key", table_name="notifications")
    op.drop_column("notifications", "pending_action_key")
