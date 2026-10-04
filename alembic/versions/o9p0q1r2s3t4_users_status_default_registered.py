"""users.status default registered

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-05-16 00:00:00.000000

Flip the server-side default for ``users.status`` from ``pending`` to
``registered`` to match the two-stage signup introduced in #120.

This is a code-only effect — ``auth.register`` always sets ``status``
explicitly — but keeping the column default in lockstep with the new
initial state avoids drift if a future code path forgets to set it.
Existing rows are untouched; ``pending`` users from before this change
remain ``pending`` (treated as having submitted under the old model).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "o9p0q1r2s3t4"
down_revision: Union[str, None] = "n8o9p0q1r2s3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "status",
        server_default="registered",
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "status",
        server_default="pending",
    )
