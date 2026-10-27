"""extended profile fields and membership date

Revision ID: 8799b59417a1
Revises: 4e548cba872d
Create Date: 2026-10-26 22:40:45.419020

Adds the optional extended profile columns to ``users`` (#92): club /
district, preferred positions and preferred languages (JSON lists in text,
#106), past experience, nationality, guardian (JSON text, as the address
is) and the membership date (a UTC-midnight date, as the date of birth
is).

The membership date is then filled for each user approved through a
review request, with the club's calendar day of the earliest approving
request (users R68b); everyone else stays blank. The body lives in
``club_server.db.backfills.membership_date`` so it is tested against the
per-function test DB. The club's time zone is read from ``CLUB_TIMEZONE``.

The downgrade drops the columns.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from club_server.db.backfills.membership_date import fill_membership_dates


# revision identifiers, used by Alembic.
revision: str = "8799b59417a1"
down_revision: Union[str, Sequence[str], None] = "4e548cba872d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEXT_COLUMNS = (
    "club_district",
    "preferred_positions",
    "past_experience",
    "nationality",
    "preferred_languages",
    "guardian",
)


def upgrade() -> None:
    """Add the extended profile columns and backfill membership dates."""
    for column in TEXT_COLUMNS:
        op.add_column("users", sa.Column(column, sa.Text(), nullable=True))
    op.add_column("users", sa.Column("membership_date", sa.BigInteger(), nullable=True))
    _ = fill_membership_dates(op.get_bind())


def downgrade() -> None:
    """Drop the extended profile columns."""
    op.drop_column("users", "membership_date")
    for column in reversed(TEXT_COLUMNS):
        op.drop_column("users", column)
