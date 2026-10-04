"""templates as items and layout, evaluations as answers (#535)

Revision ID: q7f8a9b0c1d2
Revises: p6e7f8a9b0c1
Create Date: 2026-10-01 12:00:00.000000

Replaces score categories with template items and a layout, scores, comment
and coach note with answers, and subject / author / scope columns with
``created_for``, ``created_by``, ``owner`` and a nullable ``event_id``. No
deployment holds real evaluation data, so existing rows are removed rather
than converted (`docs/evaluation_requirements.md`, "What is deliberately not
specified"). ``downgrade()`` restores the earlier, empty shape.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q7f8a9b0c1d2"
down_revision: Union[str, None] = "p6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the old content, reshape both entities, create items and answers."""
    op.execute("DELETE FROM evaluation_media")
    op.drop_table("evaluation_scores")
    op.execute("DELETE FROM evaluations")
    op.drop_table("evaluation_categories")
    op.execute("DELETE FROM evaluation_templates")

    op.drop_column("evaluation_templates", "description")
    op.drop_column("evaluation_templates", "scopes")
    op.add_column(
        "evaluation_templates",
        sa.Column("layout", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "evaluation_template_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("element", postgresql.JSONB(), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("origin_item_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["template_id"], ["evaluation_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_item_id"], ["evaluation_template_items.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_evaluation_template_items_template_id",
        "evaluation_template_items",
        ["template_id"],
    )
    op.create_index(
        "idx_evaluation_template_items_origin",
        "evaluation_template_items",
        ["origin_item_id"],
    )

    # Dropping a column drops its foreign key and its index with it.
    for column in (
        "subject_username",
        "author_username",
        "scope_type",
        "scope_event_id",
        "comment",
        "coach_note",
    ):
        op.drop_column("evaluations", column)
    op.add_column(
        "evaluations", sa.Column("created_for", sa.String(length=50), nullable=False)
    )
    op.add_column(
        "evaluations", sa.Column("created_by", sa.String(length=50), nullable=False)
    )
    op.add_column(
        "evaluations", sa.Column("owner", sa.String(length=50), nullable=True)
    )
    op.add_column("evaluations", sa.Column("event_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_evaluations_created_for",
        "evaluations",
        "users",
        ["created_for"],
        ["username"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_evaluations_created_by",
        "evaluations",
        "users",
        ["created_by"],
        ["username"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_evaluations_owner",
        "evaluations",
        "users",
        ["owner"],
        ["username"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_evaluations_event_id",
        "evaluations",
        "events",
        ["event_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_evaluations_created_for", "evaluations", ["created_for"])
    op.create_index("idx_evaluations_created_by", "evaluations", ["created_by"])
    op.create_index("idx_evaluations_owner", "evaluations", ["owner"])
    op.create_index("idx_evaluations_event", "evaluations", ["event_id"])

    op.create_table(
        "evaluation_answers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("value_num", sa.Double(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("coach_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["evaluation_id"], ["evaluations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["evaluation_template_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_evaluation_answers_evaluation_item",
        "evaluation_answers",
        ["evaluation_id", "item_id"],
        unique=True,
    )
    op.create_index(
        "idx_evaluation_answers_item_value",
        "evaluation_answers",
        ["item_id", "value_num"],
    )
    op.create_table(
        "evaluation_answer_choices",
        sa.Column("answer_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["answer_id"], ["evaluation_answers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("answer_id", "value"),
    )


def downgrade() -> None:
    """Restore the category and score shape, empty."""
    op.drop_table("evaluation_answer_choices")
    op.drop_table("evaluation_answers")
    op.execute("DELETE FROM evaluation_media")
    op.execute("DELETE FROM evaluations")
    for column in ("created_for", "created_by", "owner", "event_id"):
        op.drop_column("evaluations", column)
    op.add_column(
        "evaluations",
        sa.Column("subject_username", sa.String(length=50), nullable=False),
    )
    op.add_column(
        "evaluations",
        sa.Column("author_username", sa.String(length=50), nullable=False),
    )
    op.add_column("evaluations", sa.Column("scope_type", sa.Text(), nullable=False))
    op.add_column(
        "evaluations", sa.Column("scope_event_id", sa.Integer(), nullable=True)
    )
    op.add_column("evaluations", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column("evaluations", sa.Column("coach_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        None,
        "evaluations",
        "users",
        ["subject_username"],
        ["username"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        None,
        "evaluations",
        "users",
        ["author_username"],
        ["username"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        None, "evaluations", "events", ["scope_event_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("idx_evaluations_subject", "evaluations", ["subject_username"])
    op.create_index("idx_evaluations_author", "evaluations", ["author_username"])
    op.create_index("idx_evaluations_scope_event", "evaluations", ["scope_event_id"])

    op.drop_table("evaluation_template_items")
    op.execute("DELETE FROM evaluation_templates")
    op.drop_column("evaluation_templates", "layout")
    op.add_column(
        "evaluation_templates", sa.Column("description", sa.Text(), nullable=True)
    )
    op.add_column(
        "evaluation_templates", sa.Column("scopes", sa.Text(), nullable=False)
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
