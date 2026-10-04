"""drop_avatar_uri_from_users

Revision ID: 3106e8a2fcd5
Revises: v6w7x8y9z0a1
Create Date: 2026-05-23 21:23:36.053546

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3106e8a2fcd5"
down_revision: Union[str, Sequence[str], None] = "v6w7x8y9z0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "avatar_uri")


def downgrade() -> None:
    op.add_column(
        "users", sa.Column("avatar_uri", sa.TEXT(), autoincrement=False, nullable=True)
    )
