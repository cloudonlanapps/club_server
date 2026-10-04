"""create system_preferences table + seed notification retention default (#57)

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-05-24 09:00:00.000000

Adds the generic admin-managed key/value ``system_preferences`` table.
Seeds the first key, ``notification_info_retention_days`` = 90, which
drives the daily notification retention sweep.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "w7x8y9z0a1b2"
down_revision: Union[str, None] = "v6w7x8y9z0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_preferences",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_by",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        "INSERT INTO system_preferences (key, value, updated_at, updated_by) "
        "VALUES ('notification_info_retention_days', '90'::jsonb, "
        "(EXTRACT(EPOCH FROM NOW()) * 1000)::bigint, NULL)"
    )


def downgrade() -> None:
    op.drop_table("system_preferences")
