"""drop the retention preference row no person wrote (#518)

Revision ID: p6e7f8a9b0c1
Revises: o5d6e7f8a9b0
Create Date: 2026-09-28 18:00:00.000000

Migration ``w7x8y9z0a1b2`` (#57) seeded ``notification_info_retention_days``
= 90 with the migration time and no writer, so on a migrated database the
key read as written. Dropping that row makes it read its server default,
90, with no update time, as on a database built from the models. A value
someone set is kept. The body lives in
``club_server.db.backfills.retention_seed``.

``downgrade()`` is a no-op: without the row the key reads the same value.
"""

from typing import Sequence, Union

from alembic import op

from club_server.db.backfills.retention_seed import drop_seeded_retention_row


revision: str = "p6e7f8a9b0c1"
down_revision: Union[str, None] = "o5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _ = drop_seeded_retention_row(op.get_bind())


def downgrade() -> None:
    pass
