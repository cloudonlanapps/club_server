"""uploaded_media: add encryption-at-rest columns

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-05-20 14:00:00.000000

Adds opt-in server-side encryption-at-rest for image/PDF uploads (#150).

- ``is_encrypted``       — flag set per upload; default false.
- ``encryption_version`` — version of the algorithm/key schema; ``1`` = AES-
  256-GCM with per-file DEK wrapped by the server KEK. Null when not
  encrypted; future rotations bump this without breaking old rows.
- ``encryption_meta``    — JSON blob: ``{dek_wrapped, dek_nonce,
  payload_nonce, poster_nonce}``. Null when not encrypted.

No backfill of existing rows — plaintext uploads remain plaintext on disk.
Video uploads cannot be encrypted; the ``encrypt=true`` POST form field is
rejected for videos.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s3t4u5v6w7x8"
down_revision: Union[str, None] = "r2s3t4u5v6w7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "uploaded_media",
        sa.Column("is_encrypted", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("uploaded_media", "is_encrypted", server_default=None)
    op.add_column(
        "uploaded_media",
        sa.Column("encryption_version", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "uploaded_media",
        sa.Column("encryption_meta", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("uploaded_media", "encryption_meta")
    op.drop_column("uploaded_media", "encryption_version")
    op.drop_column("uploaded_media", "is_encrypted")
