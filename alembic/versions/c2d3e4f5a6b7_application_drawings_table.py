"""application drawings table

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-27 22:00:00.000000

Adds the ``application_drawings`` table (#128): a published application
template's form drawn once with no member in it (PDF bytes), the places of
the member's values in it (JSONB), and the publish ID and club logo it was
drawn with. One row per template, deleted with it. Templates published
before this migration get their row on their next download. The downgrade
drops it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the application_drawings table."""
    op.create_table(
        "application_drawings",
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey(
                "applications.id",
                ondelete="CASCADE",
                name="fk_application_drawings_application_id",
            ),
            primary_key=True,
        ),
        sa.Column("publish_id", sa.String(36), nullable=False),
        sa.Column("logo_key", sa.String(64), nullable=False),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("places", JSONB(), nullable=False),
        sa.Column("drawn_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    """Drop the application_drawings table."""
    op.drop_table("application_drawings")
