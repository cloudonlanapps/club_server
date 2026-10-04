"""add gender and cutoff_date_utc to groups

Revision ID: g1h2i3j4k5l6
Revises: a7c9f2b3d4e5
Create Date: 2026-04-26 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "a7c9f2b3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("groups", sa.Column("gender", sa.Text(), nullable=True))
    op.add_column(
        "groups", sa.Column("cutoff_date_utc", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("groups", "cutoff_date_utc")
    op.drop_column("groups", "gender")
