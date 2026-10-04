"""create media link tables + media_in_use view (#162)

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-05-22 11:00:00.000000

Four per-owner link tables connect ``media.uuid`` to owner rows in
``users``, ``events``, ``groups``, and ``venues``. Cascade rules:

- Owner row deleted → link row cascades (link gone; media survives).
- Media deletion blocked at DB level (``ON DELETE RESTRICT``) while any
  link references it. Service layer pre-checks for a clean 409.

The ``media_in_use`` view normalizes the four tables under a single
shape so reverse-lookup and cross-owner search can run one query.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u5v6w7x8y9z0"
down_revision: Union[str, None] = "t4u5v6w7x8y9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _common_cols() -> list[sa.Column]:
    return [
        sa.Column(
            "media_uuid",
            sa.Text(),
            sa.ForeignKey("media.uuid", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("metadata_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "user_media",
        sa.Column(
            "username",
            sa.String(length=50),
            sa.ForeignKey("users.username", ondelete="CASCADE"),
            nullable=False,
        ),
        *_common_cols(),
        sa.PrimaryKeyConstraint("username", "tag", "media_uuid", name="pk_user_media"),
    )
    op.create_index("idx_user_media_media_uuid", "user_media", ["media_uuid"])
    op.create_index("idx_user_media_username_tag", "user_media", ["username", "tag"])

    op.create_table(
        "event_media",
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_common_cols(),
        sa.PrimaryKeyConstraint("event_id", "tag", "media_uuid", name="pk_event_media"),
    )
    op.create_index("idx_event_media_media_uuid", "event_media", ["media_uuid"])
    op.create_index("idx_event_media_event_tag", "event_media", ["event_id", "tag"])

    op.create_table(
        "group_media",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_common_cols(),
        sa.PrimaryKeyConstraint("group_id", "tag", "media_uuid", name="pk_group_media"),
    )
    op.create_index("idx_group_media_media_uuid", "group_media", ["media_uuid"])
    op.create_index("idx_group_media_group_tag", "group_media", ["group_id", "tag"])

    op.create_table(
        "venue_media",
        sa.Column(
            "venue_id",
            sa.Integer(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_common_cols(),
        sa.PrimaryKeyConstraint("venue_id", "tag", "media_uuid", name="pk_venue_media"),
    )
    op.create_index("idx_venue_media_media_uuid", "venue_media", ["media_uuid"])
    op.create_index("idx_venue_media_venue_tag", "venue_media", ["venue_id", "tag"])

    op.execute(
        """
        CREATE VIEW media_in_use AS
          SELECT media_uuid, 'user'::text  AS owner_type, username::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM user_media
          UNION ALL
          SELECT media_uuid, 'event'::text AS owner_type, event_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM event_media
          UNION ALL
          SELECT media_uuid, 'group'::text AS owner_type, group_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM group_media
          UNION ALL
          SELECT media_uuid, 'venue'::text AS owner_type, venue_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM venue_media;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS media_in_use;")
    for tbl in ("venue_media", "group_media", "event_media", "user_media"):
        op.drop_table(tbl)
