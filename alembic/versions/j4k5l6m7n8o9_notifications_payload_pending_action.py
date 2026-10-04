"""notifications: JSON payload + pending-action link columns + broadcast_id

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-05-11 00:00:00.000000

Breaking change: replaces the free-text ``body`` / ``title`` columns on
``notifications`` with a structured ``payload`` JSONB column carrying the
``{ "v": 1, "type": "<domain>.<event>", "data": { ... } }`` contract.
Adds the polymorphic pending-action link columns and a nullable
``broadcast_id`` placeholder column (FK is added later in the broadcasts
migration).

No data migration — pre-prod schema reset, matching the precedent set by
revision i3j4k5l6m7n8.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "j4k5l6m7n8o9"
down_revision: Union[str, None] = "i3j4k5l6m7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("notifications", "body")
    op.drop_column("notifications", "title")

    op.add_column(
        "notifications",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.add_column(
        "notifications",
        sa.Column("pending_action_type", sa.Text(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("pending_action_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("broadcast_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_notifications_pending_action",
        "notifications",
        ["pending_action_type", "pending_action_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_pending_action", table_name="notifications")
    op.drop_column("notifications", "broadcast_id")
    op.drop_column("notifications", "pending_action_id")
    op.drop_column("notifications", "pending_action_type")
    op.drop_column("notifications", "payload")

    op.add_column(
        "notifications",
        sa.Column("title", sa.Text(), nullable=False),
    )
    op.add_column(
        "notifications",
        sa.Column("body", sa.Text(), nullable=False),
    )
