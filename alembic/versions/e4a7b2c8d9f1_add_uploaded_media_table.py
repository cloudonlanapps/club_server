"""add_uploaded_media_table

Revision ID: e4a7b2c8d9f1
Revises: d8f3e9a7b2c1
Create Date: 2026-04-22 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4a7b2c8d9f1"
down_revision: Union[str, None] = "d8f3e9a7b2c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create uploaded_media table for media file management."""
    op.create_table(
        "uploaded_media",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("uuid", sa.Text(), nullable=False, unique=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("original_extension", sa.Text(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "preserve_original", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "conversion_status", sa.Text(), nullable=False, server_default="pending"
        ),
        sa.Column("conversion_error", sa.Text(), nullable=True),
        sa.Column("conversion_params", sa.Text(), nullable=True),
        sa.Column("usage_context", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_by",
            sa.String(50),
            sa.ForeignKey("users.username"),
            nullable=False,
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("idx_uploaded_media_media_type", "uploaded_media", ["media_type"])
    op.create_index(
        "idx_uploaded_media_conversion_status", "uploaded_media", ["conversion_status"]
    )
    op.create_index("idx_uploaded_media_uploaded_by", "uploaded_media", ["uploaded_by"])


def downgrade() -> None:
    """Drop uploaded_media table."""
    op.drop_index("idx_uploaded_media_uploaded_by", table_name="uploaded_media")
    op.drop_index("idx_uploaded_media_conversion_status", table_name="uploaded_media")
    op.drop_index("idx_uploaded_media_media_type", table_name="uploaded_media")
    op.drop_table("uploaded_media")
