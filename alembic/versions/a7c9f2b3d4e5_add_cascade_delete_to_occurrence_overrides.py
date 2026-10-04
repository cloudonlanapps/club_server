"""add cascade delete to occurrence_overrides event_id FK

Revision ID: a7c9f2b3d4e5
Revises: 3e4f8b015ee5
Create Date: 2026-04-24 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7c9f2b3d4e5"
down_revision: Union[str, Sequence[str], None] = "3e4f8b015ee5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ON DELETE CASCADE to occurrence_overrides.event_id FK."""
    op.drop_constraint(
        "occurrence_overrides_event_id_fkey",
        "occurrence_overrides",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "occurrence_overrides_event_id_fkey",
        "occurrence_overrides",
        "events",
        ["event_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Remove ON DELETE CASCADE from occurrence_overrides.event_id FK."""
    op.drop_constraint(
        "occurrence_overrides_event_id_fkey",
        "occurrence_overrides",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "occurrence_overrides_event_id_fkey",
        "occurrence_overrides",
        "events",
        ["event_id"],
        ["id"],
    )
