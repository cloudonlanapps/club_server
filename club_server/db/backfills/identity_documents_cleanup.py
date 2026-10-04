"""Phase B cleanup: remove legacy identity-document rows + files (#173).

Reverses the on-disk leftovers from the Phase A copy in
``identity_documents.py`` once the Flutter client (and any other
consumer) has cut over to ``/v1/media`` + ``user_media`` for identity
documents. The new ``media`` rows are the canonical store from here on.

Steps, in order, all run inside the same Alembic transaction:

1. Identify the legacy candidate set: every ``uploaded_media`` row whose
   ``usage_context = 'user_identity_document'``. Refuse to run unless every
   candidate uuid already has a matching live row in ``media`` (Phase A
   must have completed first; otherwise we'd destroy data with no copy).
2. Delete ``user_gallery`` rows whose ``tag IN ('identity', 'identity-document')``.
3. Delete the candidate ``uploaded_media`` rows.
4. Delete every ``<uuid>*`` artifact at the ``upload_dir`` root (the
   ``upload_dir/media/`` copies are the canonical files going forward
   and are NOT touched).

Idempotent: a second run on a clean DB finds zero candidates and no-ops.
File deletes are best-effort: missing files are skipped silently.
"""

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


LEGACY_TAGS: tuple[str, ...] = ("identity", "identity-document")
LEGACY_USAGE_CONTEXT: str = "user_identity_document"


class PhaseAIncompleteError(RuntimeError):
    """Raised when the candidate set contains uuids absent from ``media``."""


def _delete_root_artifacts(uuid: str, upload_dir: Path) -> int:
    """Delete every ``<uuid>*`` file at the ``upload_dir`` root.

    Returns the number of files removed. Subdirectories (notably
    ``upload_dir/media/``) are untouched.
    """
    removed = 0
    for src in upload_dir.glob(f"{uuid}*"):
        if not src.is_file():
            continue
        try:
            src.unlink()
            removed += 1
        except OSError as exc:
            logger.warning(
                "cleanup[173]: failed to delete %s: %s",
                src,
                exc,
            )
    return removed


def cleanup(conn: Connection, upload_dir: Path) -> dict[str, int]:
    """Run the Phase B cleanup against ``conn``. Returns per-category counts.

    Categories:
    - ``gallery_rows_deleted``: legacy ``user_gallery`` rows removed.
    - ``uploaded_media_rows_deleted``: legacy ``uploaded_media`` rows removed.
    - ``uuids_processed``: distinct uuids whose root artifacts were swept.
    - ``files_deleted``: artifact files removed at the ``upload_dir`` root.

    Raises ``PhaseAIncompleteError`` if any candidate uuid is missing from
    ``media`` — aborts the whole step rather than orphaning data.
    """
    counts = {
        "gallery_rows_deleted": 0,
        "uploaded_media_rows_deleted": 0,
        "uuids_processed": 0,
        "files_deleted": 0,
    }

    candidate_uuids = [
        r["uuid"]
        for r in conn.execute(
            text("SELECT uuid FROM uploaded_media WHERE usage_context = :ctx"),
            {"ctx": LEGACY_USAGE_CONTEXT},
        )
        .mappings()
        .all()
    ]

    if candidate_uuids:
        missing = [
            r["uuid"]
            for r in conn.execute(
                text(
                    "SELECT u.uuid FROM unnest(CAST(:uuids AS text[])) AS u(uuid) "
                    "LEFT JOIN media m ON m.uuid = u.uuid "
                    "WHERE m.uuid IS NULL"
                ),
                {"uuids": candidate_uuids},
            )
            .mappings()
            .all()
        ]
        if missing:
            raise PhaseAIncompleteError(
                f"Phase A copy is missing media rows for {len(missing)} "
                f"identity-document uuid(s); refusing to delete. Examples: "
                f"{missing[:3]}",
            )

    tag_list = ", ".join(f"'{t}'" for t in LEGACY_TAGS)
    res_gallery = conn.execute(
        text(f"DELETE FROM user_gallery WHERE tag IN ({tag_list})")
    )
    counts["gallery_rows_deleted"] = res_gallery.rowcount or 0

    if candidate_uuids:
        res_media = conn.execute(
            text("DELETE FROM uploaded_media WHERE uuid = ANY(:uuids)"),
            {"uuids": candidate_uuids},
        )
        counts["uploaded_media_rows_deleted"] = res_media.rowcount or 0

        for uid in candidate_uuids:
            counts["files_deleted"] += _delete_root_artifacts(uid, upload_dir)
        counts["uuids_processed"] = len(candidate_uuids)

    logger.info("cleanup[173] summary: %s", counts)
    return counts
