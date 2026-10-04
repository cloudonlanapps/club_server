"""create broadcasts table + FK from notifications.broadcast_id

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-05-11 22:30:00.000000

Adds the ``broadcasts`` table that backs admin-authored fan-out
messages (one broadcast row → one notification per resolved
recipient). Links existing ``notifications.broadcast_id`` to it
with a real FK now that the target table exists; the column was
added as a placeholder in revision ``j4k5l6m7n8o9``.

No data migration — pre-prod schema reset.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "k5l6m7n8o9p0"
down_revision: Union[str, None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "sender_username",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "audience_selector",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("sent_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="sent"),
    )

    op.create_foreign_key(
        "fk_notifications_broadcast_id",
        "notifications",
        "broadcasts",
        ["broadcast_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_notifications_broadcast",
        "notifications",
        ["broadcast_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_broadcast", table_name="notifications")
    op.drop_constraint(
        "fk_notifications_broadcast_id", "notifications", type_="foreignkey"
    )
    op.drop_table("broadcasts")
