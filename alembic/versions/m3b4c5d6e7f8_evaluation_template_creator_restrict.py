"""evaluation_templates.created_by: RESTRICT instead of CASCADE (#490)

Revision ID: m3b4c5d6e7f8
Revises: l2a3b4c5d6e7
Create Date: 2026-09-26 18:00:00.000000

Hard-deleting a user used to delete the templates they created with them,
and failed outright when any evaluation still used one. The user service now
hands those templates to the super admin performing the delete, so the
foreign key no longer cascades. The constraint is renamed so the model can
name it.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "m3b4c5d6e7f8"
down_revision: Union[str, None] = "l2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "evaluation_templates_created_by_fkey",
        "evaluation_templates",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_evaluation_templates_created_by",
        "evaluation_templates",
        "users",
        ["created_by"],
        ["username"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_evaluation_templates_created_by",
        "evaluation_templates",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "evaluation_templates_created_by_fkey",
        "evaluation_templates",
        "users",
        ["created_by"],
        ["username"],
        ondelete="CASCADE",
    )
