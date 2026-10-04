"""Terminal cleanup: delete remaining ``uploaded_media`` rows + files (#182).

Phase B (#173) already removed the identity-document slice; this sweeps
whatever else is still in the legacy table after the v1 ``/v1/uploaded``
module has been retired across all clients. The new v2 ``media`` table
is the canonical store going forward.

Steps, in order, all inside the Alembic transaction:

1. Identify the remaining set: every row in ``uploaded_media``. Refuse
   to run unless every candidate uuid is also present in ``media`` —
   without that guarantee we'd destroy data the Phase A backfill never
   reached.
2. Delete all ``uploaded_media`` rows.
3. Delete every ``<uuid>*`` artifact at the ``upload_dir`` root for the
   removed uuids. The ``upload_dir/media/`` copies are the canonical
   files and are NOT touched.

A subsequent migration drops the table itself so the cutover can be
paused at the rows-deleted point.

Idempotent: a second run on a clean DB finds zero rows and no-ops.
"""

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


class PhaseAIncompleteError(RuntimeError):
    """Raised when remaining ``uploaded_media`` uuids are absent from ``media``."""


def _delete_root_artifacts(uuid: str, upload_dir: Path) -> int:
    removed = 0
    for src in upload_dir.glob(f"{uuid}*"):
        if not src.is_file():
            continue
        try:
            src.unlink()
            removed += 1
        except OSError as exc:
            logger.warning(
                "cleanup[182]: failed to delete %s: %s",
                src,
                exc,
            )
    return removed


def cleanup(conn: Connection, upload_dir: Path) -> dict[str, int]:
    """Run the terminal cleanup against ``conn``. Returns per-category counts.

    Categories:
    - ``uploaded_media_rows_deleted``: rows removed from ``uploaded_media``.
    - ``uuids_processed``: distinct uuids whose root artifacts were swept.
    - ``files_deleted``: artifact files removed at the ``upload_dir`` root.

    Raises ``PhaseAIncompleteError`` if any remaining uuid is missing
    from ``media`` — aborts the whole step rather than orphaning data.
    """
    counts = {
        "uploaded_media_rows_deleted": 0,
        "uuids_processed": 0,
        "files_deleted": 0,
    }

    remaining_uuids = [
        r["uuid"]
        for r in conn.execute(text("SELECT uuid FROM uploaded_media")).mappings().all()
    ]

    if not remaining_uuids:
        logger.info("cleanup[182] summary: %s", counts)
        return counts

    missing = [
        r["uuid"]
        for r in conn.execute(
            text(
                "SELECT u.uuid FROM unnest(CAST(:uuids AS text[])) AS u(uuid) "
                "LEFT JOIN media m ON m.uuid = u.uuid "
                "WHERE m.uuid IS NULL"
            ),
            {"uuids": remaining_uuids},
        )
        .mappings()
        .all()
    ]
    if missing:
        raise PhaseAIncompleteError(
            f"{len(missing)} legacy uuid(s) have no matching media row; "
            f"refusing to delete. Examples: {missing[:3]}",
        )

    res = conn.execute(text("DELETE FROM uploaded_media"))
    counts["uploaded_media_rows_deleted"] = res.rowcount or 0

    for uid in remaining_uuids:
        counts["files_deleted"] += _delete_root_artifacts(uid, upload_dir)
    counts["uuids_processed"] = len(remaining_uuids)

    logger.info("cleanup[182] summary: %s", counts)
    return counts
