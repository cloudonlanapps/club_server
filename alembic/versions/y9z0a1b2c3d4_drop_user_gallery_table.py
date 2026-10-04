"""drop user_gallery table (#173)

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-05-24 12:05:00.000000

Final step of Phase B: now that the legacy identity-document data has
been removed (``x8y9z0a1b2c3``) and the ``/v1/users/by_id/{username}/gallery*``
endpoints have been retired, drop the ``user_gallery`` table. The
indexes ``ix_user_gallery_username`` and ``ix_user_gallery_username_tag``
go with it.

Staged as a separate migration from the data cleanup so the cutover
can be paused at the rows-deleted point if anything goes wrong.

The downgrade re-creates the empty table with the same schema as the
original ``n8o9p0q1r2s3_create_user_gallery_table`` migration — the
data and the code paths that wrote to it are gone, so a re-created
table would only be useful for restoring from a backup.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "y9z0a1b2c3d4"
down_revision: Union[str, None] = "x8y9z0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_user_gallery_username_tag", table_name="user_gallery")
    op.drop_index("ix_user_gallery_username", table_name="user_gallery")
    op.drop_table("user_gallery")


def downgrade() -> None:
    op.create_table(
        "user_gallery",
        sa.Column("_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "username",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("id", name="uq_user_gallery_id"),
        sa.UniqueConstraint(
            "username",
            "tag",
            "uri",
            name="uq_user_gallery_user_tag_uri",
        ),
    )
    op.create_index("ix_user_gallery_username", "user_gallery", ["username"])
    op.create_index(
        "ix_user_gallery_username_tag",
        "user_gallery",
        ["username", "tag"],
    )
