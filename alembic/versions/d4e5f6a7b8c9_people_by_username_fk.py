"""reference organizer and coaches by username FK (#386)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-04 18:00:00.000000

``event_schedules.organizer_name`` and ``occurrence_overrides.new_organizer_name``
become FKs to ``users.username`` (``SET NULL`` on hard delete: the schedule
and the override outlive the person). The JSON list in
``event_schedules.coach_names`` becomes ``event_schedule_coaches`` rows, keyed
on ``(schedule_id, username)`` with both FKs cascading. The data is reconciled
first (``club_server.db.backfills.people_references``): organizers naming no
user are cleared and coach names that are not users are dropped.

``downgrade()`` restores the JSON column from the rows and drops the FKs.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from club_server.db.backfills.people_references import reconcile_people_references


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _ = op.create_table(
        "event_schedule_coaches",
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["event_schedules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["username"], ["users.username"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("schedule_id", "username"),
    )
    op.create_index(
        "idx_event_schedule_coaches_username", "event_schedule_coaches", ["username"]
    )

    _ = reconcile_people_references(op.get_bind())

    op.alter_column(
        "event_schedules",
        "organizer_name",
        type_=sa.String(length=50),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.create_foreign_key(
        "fk_event_schedules_organizer",
        "event_schedules",
        "users",
        ["organizer_name"],
        ["username"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_event_schedules_organizer", "event_schedules", ["organizer_name"]
    )
    op.alter_column(
        "occurrence_overrides",
        "new_organizer_name",
        type_=sa.String(length=50),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.create_foreign_key(
        "fk_occurrence_overrides_new_organizer",
        "occurrence_overrides",
        "users",
        ["new_organizer_name"],
        ["username"],
        ondelete="SET NULL",
    )
    op.drop_column("event_schedules", "coach_names")


def downgrade() -> None:
    op.add_column("event_schedules", sa.Column("coach_names", sa.Text(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT schedule_id, username FROM event_schedule_coaches "
            "ORDER BY schedule_id, position"
        )
    ).all()
    by_schedule: dict[int, list[str]] = {}
    for schedule_id, username in rows:
        by_schedule.setdefault(schedule_id, []).append(username)
    for schedule_id, names in by_schedule.items():
        _ = conn.execute(
            text("UPDATE event_schedules SET coach_names = :c WHERE id = :s"),
            {"c": json.dumps(names), "s": schedule_id},
        )
    op.drop_constraint(
        "fk_occurrence_overrides_new_organizer",
        "occurrence_overrides",
        type_="foreignkey",
    )
    op.alter_column(
        "occurrence_overrides",
        "new_organizer_name",
        type_=sa.Text(),
        existing_type=sa.String(length=50),
        existing_nullable=True,
    )
    op.drop_index("idx_event_schedules_organizer", table_name="event_schedules")
    op.drop_constraint(
        "fk_event_schedules_organizer", "event_schedules", type_="foreignkey"
    )
    op.alter_column(
        "event_schedules",
        "organizer_name",
        type_=sa.Text(),
        existing_type=sa.String(length=50),
        existing_nullable=True,
    )
    op.drop_index(
        "idx_event_schedule_coaches_username", table_name="event_schedule_coaches"
    )
    op.drop_table("event_schedule_coaches")
