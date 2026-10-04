"""uploaded_media: add access_roles column

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-05-20 13:00:00.000000

Adds per-upload access control to ``/v1/uploaded`` downloads (#149).

The column stores a JSON-encoded array of role strings from the set
{"public", "self", "admin", "coach"}. ``["public"]`` is the wide-open
sentinel; any other combination requires an authenticated caller who
satisfies at least one role in the array.

Backfill strategy: existing rows are set to ``["public"]`` to preserve
today's anonymous-download behavior. New inserts from the application
default to ``["self", "admin", "coach"]`` (staff-only) — this is the
breaking aspect of the change for clients that don't update.

After the backfill the server-side default is dropped: every insert
must carry an explicit value. The application provides one.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r2s3t4u5v6w7"
down_revision: Union[str, None] = "q1r2s3t4u5v6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add with a server-side default so existing rows backfill to public.
    op.add_column(
        "uploaded_media",
        sa.Column(
            "access_roles",
            sa.Text(),
            nullable=False,
            server_default='["public"]',
        ),
    )
    # Application now supplies access_roles on every insert; drop the
    # server-side default to keep DB-level inserts explicit.
    op.alter_column("uploaded_media", "access_roles", server_default=None)


def downgrade() -> None:
    op.drop_column("uploaded_media", "access_roles")
