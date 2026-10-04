"""drop event_aux_info; add structured eligibility, featured/media, and sessions to events

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-05-10 00:00:00.000000

Breaking change: drops the `event_aux_info` table entirely. Promotes
`is_featured`, `image_uri`, `gallery_uris` directly onto the `events`
table. Replaces the legacy free-text `eligibility` / `eligibility_note`
fields with structured `gender`, `dob_on_or_after_utc`, and
`dob_on_or_before_utc` columns, mirroring the convention adopted for
groups in #11. Adds `sessions` (JSON-encoded ordered timetable, lifted
from the legacy programme aux-info `timings` concept).

No data migration — pre-prod schema reset.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i3j4k5l6m7n8"
down_revision: Union[str, None] = "h2i3j4k5l6m7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("event_aux_info")

    op.add_column(
        "events",
        sa.Column("gender", sa.Text(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("dob_on_or_after_utc", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("dob_on_or_before_utc", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column(
            "is_featured",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "events",
        sa.Column("image_uri", sa.Text(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("gallery_uris", sa.Text(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("sessions", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "sessions")
    op.drop_column("events", "gallery_uris")
    op.drop_column("events", "image_uri")
    op.drop_column("events", "is_featured")
    op.drop_column("events", "dob_on_or_before_utc")
    op.drop_column("events", "dob_on_or_after_utc")
    op.drop_column("events", "gender")

    op.create_table(
        "event_aux_info",
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tagline", sa.Text(), nullable=True),
        sa.Column("stamp", sa.Text(), nullable=True),
        sa.Column("theme_color", sa.BigInteger(), nullable=True),
        sa.Column("image_uri", sa.Text(), nullable=True),
        sa.Column(
            "is_featured",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("gallery_uris", sa.Text(), nullable=True),
        sa.Column("highlights", sa.Text(), nullable=True),
        sa.Column("eligibility", sa.Text(), nullable=True),
        sa.Column("eligibility_note", sa.Text(), nullable=True),
        sa.Column("registration_deadline", sa.BigInteger(), nullable=True),
        sa.Column("urgency_text", sa.Text(), nullable=True),
        sa.Column("fees", sa.Integer(), nullable=True),
        sa.Column("currency", sa.Text(), server_default="INR", nullable=True),
        sa.Column("includes", sa.Text(), nullable=True),
        sa.Column("timings", sa.Text(), nullable=True),
        sa.Column("age_range", sa.Text(), nullable=True),
        sa.Column("duration", sa.Text(), nullable=True),
        sa.Column("schedule", sa.Text(), nullable=True),
        sa.Column("full_description", sa.Text(), nullable=True),
        sa.Column(
            "has_open_slots",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("batches", sa.Text(), nullable=True),
        sa.Column("facilities", sa.Text(), nullable=True),
        sa.Column("fee_structure", sa.Text(), nullable=True),
        sa.Column("package_offers", sa.Text(), nullable=True),
        sa.Column("club_membership", sa.Text(), nullable=True),
        sa.Column("offers", sa.Text(), nullable=True),
        sa.Column("contact_number", sa.Text(), nullable=True),
    )
