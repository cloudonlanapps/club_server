"""create credit system tables (#294)

Revision ID: 4ab890f12f9a
Revises: c8d9e0f1a2b3
Create Date: 2026-09-01 12:00:00.000000

Adds the three tables behind the optional credit system: ``credit_accounts``
(the pots of credit a member holds), ``credit_entries`` (the append-only
ledger those balances are derived from), and ``credit_session_charges``
(one row per member per occurrence).

Purely additive. Every foreign key points outward at ``events`` and
``users``; no existing table is altered and there is no backfill. A
deployment that leaves ``CREDIT_SYSTEM_ENABLED`` false gets the tables and
never writes to them.

The unique constraint on ``credit_session_charges`` mirrors the one on
``attendance_records`` exactly. That is what makes "a session is never
charged twice" a property of the schema: a charge may be split across
several accounts, so the constraint cannot live on the ledger entries.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4ab890f12f9a"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=8), nullable=False, unique=True),
        sa.Column(
            "membername",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "is_trial", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("valid_from", sa.BigInteger(), nullable=False),
        sa.Column("valid_until", sa.BigInteger(), nullable=False),
        sa.Column("opened_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "opened_by",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("closed_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("idx_credit_accounts_member", "credit_accounts", ["membername"])
    op.create_index("idx_credit_accounts_event", "credit_accounts", ["event_id"])
    op.create_index("idx_credit_accounts_opened", "credit_accounts", ["opened_at"])

    op.create_table(
        "credit_session_charges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_time_utc", sa.BigInteger(), nullable=False),
        sa.Column(
            "membername",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("refunded_at", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("event_id", "occurrence_time_utc", "membername"),
    )
    op.create_index(
        "idx_credit_charges_member", "credit_session_charges", ["membername"]
    )
    op.create_index(
        "idx_credit_charges_occurrence",
        "credit_session_charges",
        ["event_id", "occurrence_time_utc"],
    )

    op.create_table(
        "credit_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("credit_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("occurrence_time_utc", sa.BigInteger(), nullable=True),
        sa.Column(
            "charge_id",
            sa.Integer(),
            sa.ForeignKey("credit_session_charges.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "actor_username",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column(
            "offsets_entry_id",
            sa.Integer(),
            sa.ForeignKey("credit_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("idx_credit_entries_account", "credit_entries", ["account_id"])
    op.create_index("idx_credit_entries_charge", "credit_entries", ["charge_id"])
    op.create_index("idx_credit_entries_created", "credit_entries", ["created_at"])


def downgrade() -> None:
    op.drop_table("credit_entries")
    op.drop_table("credit_session_charges")
    op.drop_table("credit_accounts")
