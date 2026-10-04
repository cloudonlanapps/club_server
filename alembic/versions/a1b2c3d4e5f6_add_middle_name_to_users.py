"""add middle_name column to users table

Revision ID: a1b2c3d4e5f6
Revises: 5bf61f71f29e
Create Date: 2026-03-25 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "5bf61f71f29e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add middle_name column to users table."""
    op.add_column("users", sa.Column("middle_name", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove middle_name column from users table."""
    op.drop_column("users", "middle_name")
