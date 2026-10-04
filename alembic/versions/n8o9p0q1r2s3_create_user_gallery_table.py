"""create user_gallery table

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-05-15 00:00:00.000000

Adds the ``user_gallery`` table backing the per-user gallery pilot
(see issue #115). Each row is one (tag, uri) entry owned by a user.

The internal ``_id`` integer is the clustering primary key and the
target of the FK; the public ``id`` is a UUID surfaced in API
responses and URLs. ``ON DELETE CASCADE`` ties row lifetime to a
hard-deleted owner.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "n8o9p0q1r2s3"
down_revision: Union[str, None] = "m7n8o9p0q1r2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
            "username", "tag", "uri", name="uq_user_gallery_user_tag_uri"
        ),
    )
    op.create_index("ix_user_gallery_username", "user_gallery", ["username"])
    op.create_index("ix_user_gallery_username_tag", "user_gallery", ["username", "tag"])


def downgrade() -> None:
    op.drop_index("ix_user_gallery_username_tag", table_name="user_gallery")
    op.drop_index("ix_user_gallery_username", table_name="user_gallery")
    op.drop_table("user_gallery")
