"""group_join_requests: one row per (group_id, username)

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-05-20 12:00:00.000000

Aligns ``group_join_requests`` with the ``enrollments`` lifecycle model
(see ``Enrollment``'s plain ``UniqueConstraint("membername",
"event_id")`` and ``request_enrollment``'s terminal-row revival).

Before: every "Request to join" tap INSERTed a new row; only a partial
unique index ``uq_join_request_pending`` (group_id, username) WHERE
status='pending'`` prevented two concurrent pending rows. After repeated
request/cancel cycles the table accumulated unbounded terminal rows for
the same (group, user) pair, which surfaced as duplicate cards in the
member UI (an app issue).

After: at most one row per (group_id, username), tracking the current
state of that user's relationship with the group's join flow. Status
transitions in place; re-requesting after a terminal state revives the
existing row.

Upgrade steps:
  1. Collapse duplicate rows per (group_id, username), keeping the
     latest (highest ``id``). Older terminal rows are dropped from this
     table; lifecycle history lives in ``audit_log``.
  2. Drop partial index ``uq_join_request_pending``.
  3. Add plain unique constraint ``uq_group_join_request_group_user``
     on (group_id, username).

Downgrade restores the partial index but cannot resurrect the rows
collapsed in step 1. That data is recoverable from ``audit_log`` only.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q1r2s3t4u5v6"
down_revision: Union[str, None] = "p0q1r2s3t4u5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Collapse duplicates. For each (group_id, username) with more
    #    than one row, keep MAX(id) and delete the rest.
    op.execute(
        """
        DELETE FROM group_join_requests
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM group_join_requests
            GROUP BY group_id, username
        )
        """
    )

    # 2. Drop the partial unique index.
    op.drop_index("uq_join_request_pending", table_name="group_join_requests")

    # 3. Add the plain unique constraint.
    op.create_unique_constraint(
        "uq_group_join_request_group_user",
        "group_join_requests",
        ["group_id", "username"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_group_join_request_group_user",
        "group_join_requests",
        type_="unique",
    )
    op.create_index(
        "uq_join_request_pending",
        "group_join_requests",
        ["group_id", "username"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
