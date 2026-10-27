"""application library text keys and seeds table

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-28 10:00:00.000000

Adds a nullable ``key`` column to ``application_library_texts``, unique
where set (#133): the built-in clause a seeded text came from. Adds the
``application_seeds`` table, one row per seeding the server has run, so a
seeding runs once. The seeding itself runs in the server on first use, not
here. The downgrade drops both.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the key column and create the application_seeds table."""
    op.add_column(
        "application_library_texts", sa.Column("key", sa.Text(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_application_library_texts_key", "application_library_texts", ["key"]
    )
    op.create_table(
        "application_seeds",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("seeded_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    """Drop the application_seeds table and the key column."""
    op.drop_table("application_seeds")
    op.drop_constraint(
        "uq_application_library_texts_key", "application_library_texts", type_="unique"
    )
    op.drop_column("application_library_texts", "key")
