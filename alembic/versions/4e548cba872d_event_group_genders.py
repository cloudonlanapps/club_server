"""events.genders and groups.genders (#25, #26)

Revision ID: 4e548cba872d
Revises: bea7ce4acfd5
Create Date: 2026-10-14

An event's and a group's gender criterion becomes a list of genders, stored
as JSON with NULL for none (eligibility R31). The single ``gender`` column
is left in place and no longer read; its values are moved to ``genders`` by
hand on each deployment, and the column is dropped in a later release.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "4e548cba872d"
down_revision: Union[str, Sequence[str], None] = "bea7ce4acfd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("genders", sa.Text(), nullable=True))
    op.add_column("groups", sa.Column("genders", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("groups", "genders")
    op.drop_column("events", "genders")
