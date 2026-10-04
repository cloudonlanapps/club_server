"""create user_review_requests table

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-05-16 12:00:00.000000

Adds the ``user_review_requests`` table backing the admin reconsider /
self-reapply audit trail introduced in #122. Each row records an
admin's request that a pending user revisit registration, plus the
resolution that closed it. A partial unique index enforces "at most
one active row per user".

Timestamps are stored as ``BigInteger`` (epoch-ms) for consistency
with the rest of the schema (``users.created_at``, ``audit_log``,
``notifications``).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p0q1r2s3t4u5"
down_revision: Union[str, None] = "o9p0q1r2s3t4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_review_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "username",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "requested_by",
            sa.String(length=50),
            sa.ForeignKey("users.username"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.Column(
            "resolved_by",
            sa.String(length=50),
            sa.ForeignKey("users.username"),
            nullable=True,
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_user_review_requests_username",
        "user_review_requests",
        ["username"],
    )
    op.create_index(
        "user_review_requests_active_uq",
        "user_review_requests",
        ["username"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("user_review_requests_active_uq", table_name="user_review_requests")
    op.drop_index(
        "idx_user_review_requests_username", table_name="user_review_requests"
    )
    op.drop_table("user_review_requests")
