"""group kind, dob bounds, and join-request workflow

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-05-10 00:00:00.000000

Breaking change: drops `is_auto`, `age_min`, `age_max`, `cutoff_date_utc` from
`groups` and replaces them with `kind`, `dob_on_or_after_utc`,
`dob_on_or_before_utc`. Existing rows are not migrated (pre-prod schema reset).

Adds `group_join_requests` for the new admin-mediated join workflow with a
partial unique index preventing duplicate pending requests per (group, user).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h2i3j4k5l6m7"
down_revision: Union[str, None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("groups", "is_auto")
    op.drop_column("groups", "age_min")
    op.drop_column("groups", "age_max")
    op.drop_column("groups", "cutoff_date_utc")

    op.add_column(
        "groups",
        sa.Column("kind", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "groups",
        sa.Column("dob_on_or_after_utc", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "groups",
        sa.Column("dob_on_or_before_utc", sa.BigInteger(), nullable=True),
    )

    op.create_table(
        "group_join_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "username",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.BigInteger(), nullable=True),
        sa.Column("decided_by", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_join_request_group", "group_join_requests", ["group_id"])
    op.create_index("idx_join_request_username", "group_join_requests", ["username"])
    op.create_index(
        "uq_join_request_pending",
        "group_join_requests",
        ["group_id", "username"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_join_request_pending", table_name="group_join_requests")
    op.drop_index("idx_join_request_username", table_name="group_join_requests")
    op.drop_index("idx_join_request_group", table_name="group_join_requests")
    op.drop_table("group_join_requests")

    op.drop_column("groups", "dob_on_or_before_utc")
    op.drop_column("groups", "dob_on_or_after_utc")
    op.drop_column("groups", "kind")

    op.add_column(
        "groups", sa.Column("cutoff_date_utc", sa.BigInteger(), nullable=True)
    )
    op.add_column("groups", sa.Column("age_max", sa.Integer(), nullable=True))
    op.add_column("groups", sa.Column("age_min", sa.Integer(), nullable=True))
    op.add_column(
        "groups",
        sa.Column("is_auto", sa.Integer(), nullable=False, server_default="0"),
    )
