"""add partial unique index on users.email (#204)

Revision ID: 636d540d2ae7
Revises: fd2135bfaf16
Create Date: 2026-05-26 00:00:00.000000

Closes the TOCTOU race in user creation: previously email uniqueness was
guarded only by an application-level pre-flight check, so two concurrent
requests with the same email could both commit. This adds a case-insensitive
partial unique index over live (non-deleted) rows, making the database the
sole authority. Soft-deleted rows are excluded so a deleted user frees their
email for reuse, matching the `deleted_at IS NULL` lookup semantics.

DEPLOYMENT PREREQUISITE: this migration FAILS if the table already contains
two non-deleted rows whose emails collide case-insensitively. Audit and
de-duplicate production/staging data BEFORE running it:

    SELECT lower(email), count(*) FROM users
    WHERE email IS NOT NULL AND deleted_at IS NULL
    GROUP BY lower(email) HAVING count(*) > 1;
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "636d540d2ae7"
down_revision: Union[str, None] = "fd2135bfaf16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_users_email_active",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_active", table_name="users")
