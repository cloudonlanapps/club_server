"""public staff listing table; drop users.display_order (#332)

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-05 10:00:00.000000

``users.display_order`` carried two facts by sign: position on the public
staff page and, when negative, guest status. Both move to a curation row
of their own. Existing values are carried across: a negative order
becomes a guest at its absolute position, a positive one a position.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "public_staff_listing",
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("username"),
    )
    op.execute(
        """
        INSERT INTO public_staff_listing
            (username, position, is_guest, is_hidden, created_at, updated_at)
        SELECT username,
               ABS(display_order),
               display_order < 0,
               FALSE,
               (EXTRACT(EPOCH FROM NOW()) * 1000)::bigint,
               (EXTRACT(EPOCH FROM NOW()) * 1000)::bigint
        FROM users
        WHERE display_order IS NOT NULL
        """
    )
    op.drop_column("users", "display_order")


def downgrade() -> None:
    op.add_column("users", sa.Column("display_order", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE users u
        SET display_order = CASE WHEN l.is_guest THEN -COALESCE(l.position, 1)
                                 ELSE l.position END
        FROM public_staff_listing l
        WHERE l.username = u.username
        """
    )
    op.drop_table("public_staff_listing")
