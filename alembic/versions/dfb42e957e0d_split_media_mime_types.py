"""split media mime types: original vs actual

Revision ID: dfb42e957e0d
Revises: i9d0e1f2a3b4
Create Date: 2026-09-07 02:10:00.000000

``media.mime_type`` was named for the type of the stored file and held the type
of the *upload*, which diverge whenever the file is converted — a PNG uploaded
without ``preserveOriginal`` is stored and served as WebP while the column
still said ``image/png`` (#426, following #331).

The old value is not lost: it is what the column is renamed to. The new
``mime_type`` is backfilled from the same rule ``resolve_media_file_path``
uses, so no filesystem access is needed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "dfb42e957e0d"
down_revision: Union[str, Sequence[str], None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The extension of the artifact a download serves, per media_type. Mirrors
# ``services.media.resolve_media_file_path``; keep the two in step.
_SERVED_EXTENSION = """
    CASE
        WHEN media_type = 'pdf' THEN 'pdf'
        WHEN preserve_original <> 0 THEN original_extension
        WHEN media_type = 'image' THEN 'webp'
        ELSE 'mp4'
    END
"""

# Mirrors ``services.media_mime.MIME_BY_EXTENSION`` (without the leading dot).
# A row whose extension is not here keeps its uploaded type rather than being
# guessed at.
_MIME_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "webp": "image/webp",
    "mp4": "video/mp4",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "pdf": "application/pdf",
}


def upgrade() -> None:
    op.alter_column("media", "mime_type", new_column_name="original_mime_type")
    op.add_column("media", sa.Column("mime_type", sa.Text(), nullable=True))

    whens = "\n".join(
        f"WHEN LOWER({_SERVED_EXTENSION}) = '{ext}' THEN '{mime}'"
        for ext, mime in _MIME_BY_EXTENSION.items()
    )
    op.execute(
        f"""
        UPDATE media SET mime_type = CASE
            {whens}
            ELSE original_mime_type
        END
        """
    )

    op.alter_column("media", "mime_type", nullable=False)


def downgrade() -> None:
    op.drop_column("media", "mime_type")
    op.alter_column("media", "original_mime_type", new_column_name="mime_type")
