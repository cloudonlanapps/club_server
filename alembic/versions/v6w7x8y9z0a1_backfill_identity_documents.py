"""backfill identity documents from legacy stack into media + user_media (#170)

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-05-23 12:00:00.000000

Phase A of the identity-document migration: copy each legacy
``user_gallery`` row whose tag is ``identity`` / ``identity-document`` into
a ``media`` row + ``user_media`` link row with tag ``identity_document`` and
``access_roles=['self', 'admin']``. Legacy rows are NOT removed — that is
Phase B, tracked separately once the Flutter client (an app issue)
has cut over to the new endpoints.

Files on disk are physically copied from ``<upload_dir>/<uuid>.<ext>`` to
``<upload_dir>/media/<uuid>.<ext>`` (every ``<uuid>*`` artifact found),
preserving the originals for the legacy ``/v1/uploaded`` endpoints to
continue serving during cutover.

Idempotent: ``ON CONFLICT DO NOTHING`` on both inserts; file copies skip
destinations that already match by size. Safe to re-run after a partial
failure (e.g. ``alembic upgrade`` killed mid-run, then resumed).

The body lives in ``club_server.db.backfills.identity_documents`` so it can
be unit-tested against the per-function test DB session without spinning
up Alembic. See ``tests/test_backfill_identity_documents.py``.
"""

from typing import Sequence, Union

from alembic import op

from club_server.db.backfills import upload_dir_from_env
from club_server.db.backfills.identity_documents import backfill, revert


revision: str = "v6w7x8y9z0a1"
down_revision: Union[str, None] = "u5v6w7x8y9z0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    backfill(conn, upload_dir_from_env())


def downgrade() -> None:
    conn = op.get_bind()
    revert(conn)
