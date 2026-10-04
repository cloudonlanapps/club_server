"""add_display_order_to_users

Revision ID: d8f3e9a7b2c1
Revises: c7b2d8f680d6
Create Date: 2026-03-25 22:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d8f3e9a7b2c1"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add display_order column to users table.

    Coaches with display_order=NULL are hidden from public listing.
    Lower numbers appear first (e.g., 1, 2, 3).
    """
    op.add_column("users", sa.Column("display_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove display_order column from users table."""
    op.drop_column("users", "display_order")
