"""Add gender and address to users

Revision ID: f5a3b7c9d2e4
Revises: e4a7b2c8d9f1
Create Date: 2026-04-22

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f5a3b7c9d2e4"
down_revision: Union[str, None] = "e4a7b2c8d9f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("gender", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("address", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "address")
    op.drop_column("users", "gender")
