"""cleanup remaining legacy uploaded_media rows + files (#182)

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-05-24 12:10:00.000000

Terminal data cleanup for the legacy ``/v1/uploaded`` module: now that
every client reads from the v2 ``/v1/media`` stack and the Phase B
identity-document slice has been removed (#173), delete every
remaining row in ``uploaded_media`` and every ``<uuid>*`` artifact at
the ``upload_dir`` root for those rows. The ``upload_dir/media/`` tree
is the canonical store and is untouched.

Pre-check: refuses to run if any remaining uuid is missing from the
``media`` table — would indicate uncopied data; abort rather than
destroy. (Phase A backfill #170 must have produced the matching
``media`` row for every legacy uuid by this point.)

The body lives in ``club_server.db.backfills.uploaded_media_cleanup``
so it can be unit-tested against the per-function test DB session
without spinning up Alembic.

``downgrade()`` is intentionally a no-op: deleted rows can only be
restored from a backup, and deleted on-disk files cannot be recovered.
"""

from typing import Sequence, Union

from alembic import op

from club_server.db.backfills import upload_dir_from_env
from club_server.db.backfills.uploaded_media_cleanup import cleanup


revision: str = "z0a1b2c3d4e5"
down_revision: Union[str, None] = "y9z0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cleanup(conn, upload_dir_from_env())


def downgrade() -> None:
    pass
