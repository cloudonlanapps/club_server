"""event_marketing table (#410)

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-09-05 11:00:00.000000

The extended marketing block, 1:1 on events, for deployments that run the
Event Marketing module. Cascades with the event.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_marketing",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("duration_text", sa.Text(), nullable=True),
        sa.Column("schedule_text", sa.Text(), nullable=True),
        sa.Column("eligibility_text", sa.Text(), nullable=True),
        sa.Column("eligibility_note", sa.Text(), nullable=True),
        sa.Column("registration_deadline_utc", sa.BigInteger(), nullable=True),
        sa.Column("has_open_slots", sa.Boolean(), nullable=True),
        sa.Column("urgency_text", sa.Text(), nullable=True),
        sa.Column("contact_number", sa.String(length=32), nullable=True),
        sa.Column("fee", sa.Integer(), nullable=True),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="INR"
        ),
        sa.Column("fee_structure", sa.Text(), nullable=True),
        sa.Column("package_offers", sa.Text(), nullable=True),
        sa.Column("offers", sa.Text(), nullable=True),
        sa.Column("club_membership", sa.Text(), nullable=True),
        sa.Column("facilities", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("event_marketing")
