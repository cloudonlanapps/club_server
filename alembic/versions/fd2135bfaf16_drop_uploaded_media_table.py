"""drop uploaded_media table (#182)

Revision ID: fd2135bfaf16
Revises: z0a1b2c3d4e5
Create Date: 2026-05-24 12:15:00.000000

Final step of the legacy ``/v1/uploaded`` retirement: now that the
last data and on-disk artifacts are gone (``z0a1b2c3d4e5``) and the
router / service / model code paths have been removed from the app,
drop the ``uploaded_media`` table and its indexes.

Staged as a separate migration from the data cleanup so the cutover
can be paused at the rows-deleted point if anything goes wrong.

``downgrade()`` re-creates the empty table with the same schema as the
original create migration (``e4a7b2c8d9f1``) plus the columns added by
later ones (``r2s3t4u5v6w7`` for ``access_roles`` and
``s3t4u5v6w7x8`` for the encryption columns). Restoring data requires
a backup.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fd2135bfaf16"
down_revision: Union[str, None] = "z0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("idx_uploaded_media_uploaded_by", table_name="uploaded_media")
    op.drop_index("idx_uploaded_media_conversion_status", table_name="uploaded_media")
    op.drop_index("idx_uploaded_media_media_type", table_name="uploaded_media")
    op.drop_table("uploaded_media")


def downgrade() -> None:
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
        sa.Column(
            "access_roles", sa.Text(), nullable=False, server_default='["public"]'
        ),
        sa.Column(
            "is_encrypted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("encryption_version", sa.Integer(), nullable=True),
        sa.Column("encryption_meta", sa.Text(), nullable=True),
    )
    op.create_index("idx_uploaded_media_media_type", "uploaded_media", ["media_type"])
    op.create_index(
        "idx_uploaded_media_conversion_status", "uploaded_media", ["conversion_status"]
    )
    op.create_index("idx_uploaded_media_uploaded_by", "uploaded_media", ["uploaded_by"])
