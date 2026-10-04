"""create media table (v2 media foundation)

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-05-22 10:00:00.000000

Parallel v2 media table — see #161 / #163. Lives alongside ``uploaded_media``
and does NOT migrate or touch existing data.

Key differences from ``uploaded_media``:
- ``uploaded_by`` is ``ON DELETE SET NULL`` and nullable. Uploads outlive
  uploaders (#157 root fix); a deleted user does not cascade-delete media.
- No ``usage_context`` column. Link-table metadata replaces it (#162).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "t4u5v6w7x8y9"
down_revision: Union[str, None] = "s3t4u5v6w7x8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
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
        sa.Column(
            "uploaded_by",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "access_roles",
            sa.Text(),
            nullable=False,
            server_default='["public"]',
        ),
        sa.Column("is_encrypted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("encryption_version", sa.SmallInteger(), nullable=True),
        sa.Column("encryption_meta", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("idx_media_media_type", "media", ["media_type"])
    op.create_index("idx_media_conversion_status", "media", ["conversion_status"])
    op.create_index("idx_media_uploaded_by", "media", ["uploaded_by"])
    op.create_index("idx_media_deleted_at", "media", ["deleted_at"])

    # Application supplies access_roles on every insert; drop the server-side
    # default to keep DB-level inserts explicit.
    op.alter_column("media", "access_roles", server_default=None)
    op.alter_column("media", "preserve_original", server_default=None)
    op.alter_column("media", "conversion_status", server_default=None)
    op.alter_column("media", "is_encrypted", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_media_deleted_at", table_name="media")
    op.drop_index("idx_media_uploaded_by", table_name="media")
    op.drop_index("idx_media_conversion_status", table_name="media")
    op.drop_index("idx_media_media_type", table_name="media")
    op.drop_table("media")
