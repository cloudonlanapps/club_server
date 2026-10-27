"""application library texts table

Revision ID: b1c2d3e4f5a6
Revises: afa0ffbd8476
Create Date: 2026-10-27 18:00:00.000000

Adds the ``application_library_texts`` table (#126): the club's own texts
for application templates, offered beside the built-in clauses. Each has a
kind, a short name, a heading (a rule's) and its text per language, in
JSONB. The downgrade drops it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "afa0ffbd8476"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the application_library_texts table."""
    op.create_table(
        "application_library_texts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("heading", JSONB(), nullable=True),
        sa.Column("text", JSONB(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(50),
            sa.ForeignKey(
                "users.username",
                ondelete="SET NULL",
                name="fk_application_library_texts_created_by",
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    """Drop the application_library_texts table."""
    op.drop_table("application_library_texts")
