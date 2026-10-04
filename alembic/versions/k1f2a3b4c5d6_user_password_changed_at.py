"""add password_changed_at to users (#461)

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-09-26 12:00:00.000000

Set on every password change or reset; a token issued before it is refused.
Existing rows stay NULL, so tokens already issued keep working until the
user's password next changes.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "k1f2a3b4c5d6"
down_revision: Union[str, None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("password_changed_at", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
