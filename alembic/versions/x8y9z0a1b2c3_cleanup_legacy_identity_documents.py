"""cleanup legacy identity-document rows + files (#173)

Revision ID: x8y9z0a1b2c3
Revises: 3c045939d571
Create Date: 2026-05-24 12:00:00.000000

Phase B of the identity-document migration: now that the Phase A copy
in ``v6w7x8y9z0a1`` has produced the canonical ``media`` + ``user_media``
rows and every client reads from the new endpoints, remove the legacy
rows and on-disk artifacts:

- Delete ``user_gallery`` rows tagged ``identity`` / ``identity-document``.
- Delete ``uploaded_media`` rows whose ``usage_context = 'user_identity_document'``.
- Delete every ``<uuid>*`` artifact at the ``upload_dir`` root for the
  removed uuids. The ``upload_dir/media/`` copies are the canonical
  files and are NOT touched.

The ``user_gallery`` table itself is dropped in the next migration
(``y9z0a1b2c3d4``) so the cutover can be staged.

Pre-check: refuses to run if any candidate uuid is missing from the new
``media`` table (would indicate Phase A never ran in this environment;
abort rather than destroy).

The body lives in ``club_server.db.backfills.identity_documents_cleanup``
so it can be unit-tested against the per-function test DB session
without spinning up Alembic.

``downgrade()`` is best-effort: rows can be restored only if a backup
exists upstream, and deleted on-disk files cannot be recovered.
"""

from typing import Sequence, Union

from alembic import op

from club_server.db.backfills import upload_dir_from_env
from club_server.db.backfills.identity_documents_cleanup import cleanup


revision: str = "x8y9z0a1b2c3"
down_revision: Union[str, None] = "3c045939d571"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cleanup(conn, upload_dir_from_env())


def downgrade() -> None:
    # Phase B is destructive: deleted rows can only be restored from a
    # backup, and on-disk files at the upload_dir root cannot be recovered.
    # The downgrade is intentionally a no-op so it does not silently
    # pretend to have undone the cleanup.
    pass
