"""Create evaluation tables (#302).

Restores evaluations, removed wholesale in 0126c9b, with the shape the
requirements settle on: scope columns, structured scores validated against
a template's declared categories, soft delete on both entities, and a
media link table following the four in ``media_links.py``.

Revision ID: a1e2v3a4l5u6
Revises: 5bc9d0e1f2a3
"""

import sqlalchemy as sa
from alembic import op

revision = "a1e2v3a4l5u6"
down_revision = "5bc9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the five evaluation tables."""
    op.create_table(
        "evaluation_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_evaluation_templates_deleted_at", "evaluation_templates", ["deleted_at"]
    )

    op.create_table(
        "evaluation_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("min_value", sa.Integer(), nullable=False),
        sa.Column("max_value", sa.Integer(), nullable=False),
        sa.Column("default_value", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"], ["evaluation_templates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_evaluation_categories_template_id",
        "evaluation_categories",
        ["template_id"],
    )
    op.create_index(
        "uq_evaluation_categories_template_key",
        "evaluation_categories",
        ["template_id", "key"],
        unique=True,
    )

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subject_username", sa.String(length=50), nullable=False),
        sa.Column("author_username", sa.String(length=50), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_event_id", sa.Integer(), nullable=True),
        sa.Column("period_start_utc", sa.BigInteger(), nullable=True),
        sa.Column("period_end_utc", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("coach_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["subject_username"], ["users.username"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_username"], ["users.username"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["evaluation_templates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["scope_event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_evaluations_subject", "evaluations", ["subject_username"])
    op.create_index("idx_evaluations_author", "evaluations", ["author_username"])
    op.create_index("idx_evaluations_scope_event", "evaluations", ["scope_event_id"])
    op.create_index("idx_evaluations_status", "evaluations", ["status"])
    op.create_index("idx_evaluations_deleted_at", "evaluations", ["deleted_at"])

    op.create_table(
        "evaluation_scores",
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_id"], ["evaluations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["evaluation_categories.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("evaluation_id", "category_id"),
    )

    op.create_table(
        "evaluation_media",
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("media_uuid", sa.Text(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("metadata_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_id"], ["evaluations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["media_uuid"], ["media.uuid"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("evaluation_id", "media_uuid", "tag"),
    )
    op.create_index(
        "idx_evaluation_media_media_uuid", "evaluation_media", ["media_uuid"]
    )

    # The MEDIA_IN_USE soft-delete guard reads this view rather than the
    # owner registry, so a new owner must join it or its links silently
    # fail to block deletion of the media they point at (#162).
    op.execute("DROP VIEW IF EXISTS media_in_use;")
    op.execute(
        """
        CREATE VIEW media_in_use AS
          SELECT media_uuid, 'user'::text  AS owner_type, username::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM user_media
          UNION ALL
          SELECT media_uuid, 'event'::text AS owner_type, event_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM event_media
          UNION ALL
          SELECT media_uuid, 'group'::text AS owner_type, group_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM group_media
          UNION ALL
          SELECT media_uuid, 'venue'::text AS owner_type, venue_id::text  AS owner_id, tag, metadata_value, created_at, updated_at FROM venue_media
          UNION ALL
          SELECT media_uuid, 'evaluation'::text AS owner_type, evaluation_id::text AS owner_id, tag, metadata_value, created_at, updated_at FROM evaluation_media;
        """
    )


def downgrade() -> None:
    """Drop the five evaluation tables and restore the four-owner view."""
    op.execute("DROP VIEW IF EXISTS media_in_use;")
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
    op.drop_index("idx_evaluation_media_media_uuid", table_name="evaluation_media")
    op.drop_table("evaluation_media")
    op.drop_table("evaluation_scores")
    op.drop_index("idx_evaluations_deleted_at", table_name="evaluations")
    op.drop_index("idx_evaluations_status", table_name="evaluations")
    op.drop_index("idx_evaluations_scope_event", table_name="evaluations")
    op.drop_index("idx_evaluations_author", table_name="evaluations")
    op.drop_index("idx_evaluations_subject", table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_index(
        "uq_evaluation_categories_template_key", table_name="evaluation_categories"
    )
    op.drop_index(
        "idx_evaluation_categories_template_id", table_name="evaluation_categories"
    )
    op.drop_table("evaluation_categories")
    op.drop_index(
        "idx_evaluation_templates_deleted_at", table_name="evaluation_templates"
    )
    op.drop_table("evaluation_templates")
