"""add_is_public_profile_to_users

Revision ID: c8d9e0f1a2b3
Revises: a3f1c2d4e5b6
Create Date: 2026-06-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "a3f1c2d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_public_profile flag to users.

    A coach opts their profile into public visibility (the `/public`
    endpoints) by setting this true on their own profile. Defaults to false
    for every existing and new user.
    """
    op.add_column(
        "users",
        sa.Column(
            "is_public_profile",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Remove is_public_profile column from users table."""
    op.drop_column("users", "is_public_profile")
