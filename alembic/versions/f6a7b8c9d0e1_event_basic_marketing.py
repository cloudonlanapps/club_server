"""basic marketing fields on events (#409)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-05 09:00:00.000000

``short_description``, ``stamp``, ``highlights`` and ``includes`` are the
presentation fields every club's public site shows on an event card. They
live on the event itself; the commercial detail is the gated
event-marketing module (#410).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("short_description", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("stamp", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("highlights", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("includes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "includes")
    op.drop_column("events", "highlights")
    op.drop_column("events", "stamp")
    op.drop_column("events", "short_description")
