"""drop_image_uri_from_events_and_venues

Revision ID: a3f1c2d4e5b6
Revises: 636d540d2ae7
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f1c2d4e5b6"
down_revision: Union[str, Sequence[str], None] = "636d540d2ae7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("events", "image_uri")
    op.drop_column("venues", "image_uri")


def downgrade() -> None:
    op.add_column(
        "venues", sa.Column("image_uri", sa.TEXT(), autoincrement=False, nullable=True)
    )
    op.add_column(
        "events", sa.Column("image_uri", sa.TEXT(), autoincrement=False, nullable=True)
    )
