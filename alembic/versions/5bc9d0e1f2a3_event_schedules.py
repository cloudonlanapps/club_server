"""move an event's timetable to event_schedules (#388)

Revision ID: 5bc9d0e1f2a3
Revises: 4ab890f12f9a
Create Date: 2026-09-03 12:00:00.000000

An event keeps one id for life; what changes over time — its times, rule,
venue, staffing and timetable — moves to ``event_schedules``, a contiguous
sequence of periods per event (``docs/event_schedule_model.md``).

Backfill is one schedule row per existing event, carrying the event's
current timetable with ``effective_from = start_time`` and
``effective_until = until_time``. Programmes have not been released, so no
split chain exists; a ``continued_*`` link that did exist stays two events.
The three chain columns and the timetable columns are then dropped.

Also records a cancelled occurrence's reason on its override (one-off R8),
and adds the stamps the credit sweep needs: ``events.credit_released_at``
(a termination release happens once) and, on enrollments, the disposition a
departing member's admin stated and the instant it may be applied.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5bc9d0e1f2a3"
down_revision: Union[str, None] = "4ab890f12f9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TIMETABLE_COLUMNS = (
    "start_time",
    "end_time",
    "rrule",
    "venue_id",
    "organizer_name",
    "coach_names",
    "sessions",
)
_CHAIN_COLUMNS = ("until_time", "continued_as_event_id", "continued_from_event_id")


def upgrade() -> None:
    op.create_table(
        "event_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.BigInteger(), nullable=False),
        sa.Column("effective_until", sa.BigInteger(), nullable=True),
        sa.Column("start_time", sa.BigInteger(), nullable=False),
        sa.Column("end_time", sa.BigInteger(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=True),
        sa.Column("venue_id", sa.Integer(), sa.ForeignKey("venues.id"), nullable=False),
        sa.Column("organizer_name", sa.Text(), nullable=True),
        sa.Column("coach_names", sa.Text(), nullable=True),
        sa.Column("sessions", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "idx_event_schedules_event",
        "event_schedules",
        ["event_id", "effective_from"],
    )
    op.create_index("idx_event_schedules_venue", "event_schedules", ["venue_id"])

    op.execute(
        """
        INSERT INTO event_schedules (
            event_id, effective_from, effective_until, start_time, end_time,
            rrule, venue_id, organizer_name, coach_names, sessions,
            created_at, updated_at
        )
        SELECT id, start_time, until_time, start_time, end_time,
               rrule, venue_id, organizer_name, coach_names, sessions,
               created_at, updated_at
        FROM events
        ORDER BY id
        """
    )

    for column in _CHAIN_COLUMNS + _TIMETABLE_COLUMNS:
        op.drop_column("events", column)

    op.add_column(
        "events", sa.Column("credit_released_at", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "occurrence_overrides", sa.Column("cancel_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "enrollments", sa.Column("pending_disposition", sa.Text(), nullable=True)
    )
    op.add_column(
        "enrollments", sa.Column("settle_after_utc", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("enrollments", "settle_after_utc")
    op.drop_column("enrollments", "pending_disposition")
    op.drop_column("occurrence_overrides", "cancel_reason")
    op.drop_column("events", "credit_released_at")

    op.add_column("events", sa.Column("start_time", sa.BigInteger(), nullable=True))
    op.add_column("events", sa.Column("end_time", sa.BigInteger(), nullable=True))
    op.add_column("events", sa.Column("rrule", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("venue_id", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("organizer_name", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("coach_names", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("sessions", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("until_time", sa.BigInteger(), nullable=True))
    op.add_column(
        "events", sa.Column("continued_as_event_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "events", sa.Column("continued_from_event_id", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE events e SET
            start_time = s.start_time, end_time = s.end_time, rrule = s.rrule,
            venue_id = s.venue_id, organizer_name = s.organizer_name,
            coach_names = s.coach_names, sessions = s.sessions,
            until_time = s.effective_until
        FROM (
            SELECT DISTINCT ON (event_id) *
            FROM event_schedules ORDER BY event_id, effective_from DESC
        ) s
        WHERE s.event_id = e.id
        """
    )
    for column in ("start_time", "end_time", "venue_id"):
        op.alter_column("events", column, nullable=False)
    op.create_foreign_key(None, "events", "venues", ["venue_id"], ["id"])
    op.drop_index("idx_event_schedules_venue", table_name="event_schedules")
    op.drop_index("idx_event_schedules_event", table_name="event_schedules")
    op.drop_table("event_schedules")
