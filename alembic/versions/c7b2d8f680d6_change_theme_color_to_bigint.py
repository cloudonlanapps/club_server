"""change theme_color to bigint

Revision ID: c7b2d8f680d6
Revises: b85fba8c687a
Create Date: 2026-03-18 12:03:16.347391

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7b2d8f680d6"
down_revision: Union[str, Sequence[str], None] = "b85fba8c687a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "event_aux_info",
        "theme_color",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "event_aux_info",
        "theme_color",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
