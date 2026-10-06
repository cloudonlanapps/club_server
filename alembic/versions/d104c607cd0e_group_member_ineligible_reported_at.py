"""group_members.ineligible_reported_at (#17)

Revision ID: d104c607cd0e
Revises: 278700baeb87
Create Date: 2026-10-06

When the daily scan told the admins that a semi-auto member no longer
meets the group's criteria. NULL while the member matches, so every
existing membership starts unreported.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d104c607cd0e"
down_revision: Union[str, Sequence[str], None] = "278700baeb87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "group_members",
        sa.Column("ineligible_reported_at", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("group_members", "ineligible_reported_at")
