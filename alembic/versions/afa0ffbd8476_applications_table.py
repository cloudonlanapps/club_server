"""applications table

Revision ID: afa0ffbd8476
Revises: 8799b59417a1
Create Date: 2026-10-26 23:03:09.781754

Adds the ``applications`` table (#93): admin-defined application templates
whose printed texts sit in JSONB columns, a status (draft / saved /
published), the layout decided on save, and soft delete. The downgrade
drops it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "afa0ffbd8476"
down_revision: Union[str, Sequence[str], None] = "8799b59417a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the applications table."""
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("languages", JSONB(), nullable=False),
        sa.Column("facts", JSONB(), nullable=False),
        sa.Column("organisation", JSONB(), nullable=True),
        sa.Column("title", JSONB(), nullable=True),
        sa.Column("subtitle", JSONB(), nullable=True),
        sa.Column("header", JSONB(), nullable=False),
        sa.Column("age_categories", JSONB(), nullable=False),
        sa.Column("selections", JSONB(), nullable=False),
        sa.Column("declaration", JSONB(), nullable=True),
        sa.Column("form_footer", JSONB(), nullable=True),
        sa.Column("terms", JSONB(), nullable=False),
        sa.Column("terms_notes", JSONB(), nullable=False),
        sa.Column("label_overrides", JSONB(), nullable=False),
        sa.Column("layout", JSONB(), nullable=True),
        sa.Column(
            "created_by",
            sa.String(50),
            sa.ForeignKey(
                "users.username",
                ondelete="SET NULL",
                name="fk_applications_created_by",
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("idx_applications_status", "applications", ["status"])
    op.create_index("idx_applications_deleted_at", "applications", ["deleted_at"])


def downgrade() -> None:
    """Drop the applications table."""
    op.drop_index("idx_applications_deleted_at", table_name="applications")
    op.drop_index("idx_applications_status", table_name="applications")
    op.drop_table("applications")
