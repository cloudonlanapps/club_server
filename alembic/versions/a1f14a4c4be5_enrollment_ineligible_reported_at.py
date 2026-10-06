"""enrollments.ineligible_reported_at (#19)

Revision ID: a1f14a4c4be5
Revises: d104c607cd0e
Create Date: 2026-10-06

When the daily scan told the admins that an enrolled programme member no
longer meets the programme's criteria. NULL while the member matches, so
every existing enrolment starts unreported.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1f14a4c4be5"
down_revision: Union[str, Sequence[str], None] = "d104c607cd0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "enrollments",
        sa.Column("ineligible_reported_at", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("enrollments", "ineligible_reported_at")
