"""merge avatar-drop and system-prefs branches

Revision ID: 3c045939d571
Revises: 3106e8a2fcd5, w7x8y9z0a1b2
Create Date: 2026-05-24 12:08:49.250498

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "3c045939d571"
down_revision: Union[str, Sequence[str], None] = ("3106e8a2fcd5", "w7x8y9z0a1b2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
