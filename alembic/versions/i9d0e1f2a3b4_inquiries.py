"""inquiries table (#407)

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-09-05 12:00:00.000000

Contact-form and interest submissions from the public website.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inquiries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("handled_at", sa.BigInteger(), nullable=True),
        sa.Column("handled_by", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(
            ["handled_by"], ["users.username"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inquiries_created_at", "inquiries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_inquiries_created_at", table_name="inquiries")
    op.drop_table("inquiries")
