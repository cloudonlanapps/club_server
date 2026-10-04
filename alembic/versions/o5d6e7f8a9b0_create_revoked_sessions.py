"""create revoked_sessions: logout ends a login session (#510)

Revision ID: o5d6e7f8a9b0
Revises: n4c5d6e7f8a9
Create Date: 2026-09-28 12:00:00.000000

Tokens now carry the ``sid`` of the login session they belong to. Logout
records that session here, and every token carrying its ``sid`` is refused
until ``expires_at``, after which none could verify anyway.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "o5d6e7f8a9b0"
down_revision: Union[str, None] = "n4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "revoked_sessions",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("revoked_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "idx_revoked_sessions_expires_at", "revoked_sessions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_revoked_sessions_expires_at", table_name="revoked_sessions")
    op.drop_table("revoked_sessions")
