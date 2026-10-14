"""inquiries.message is optional (#41)

Revision ID: bea7ce4acfd5
Revises: c4e7a91b2d13
Create Date: 2026-10-14

Whether a form needs a message is the client's to decide, so an inquiry
may be stored without one. Existing rows keep theirs. A downgrade gives an
inquiry without a message an empty one, since the column then requires it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "bea7ce4acfd5"
down_revision: Union[str, Sequence[str], None] = "c4e7a91b2d13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("inquiries", "message", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE inquiries SET message = '' WHERE message IS NULL")
    op.alter_column("inquiries", "message", existing_type=sa.Text(), nullable=False)
