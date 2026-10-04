"""Phase A backfill: legacy identity-document data → v2 media stack (#170).

Copies each ``user_gallery`` row whose tag is ``identity`` or
``identity-document`` into a matching ``media`` row + ``user_media_links``
row with tag ``identity_document`` and ``access_roles=['self', 'admin']``.
Legacy rows are NOT touched (Phase B removes them once the Flutter client
has cut over — tracked in an app issue).

Files on disk are physically copied from ``<upload_dir>/<uuid>.<ext>`` to
``<upload_dir>/media/<uuid>.<ext>`` (and every other ``<uuid>*`` artifact
that exists — posters, animated previews, ``.enc`` variants). Legacy files
stay where they are so the legacy ``/v1/uploaded`` endpoints keep working
during cutover.

Idempotent: ``ON CONFLICT DO NOTHING`` on both inserts, file copy skipped
when the destination already matches by size. Safe to re-run if a prior
``alembic upgrade`` died partway.
"""

import json
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


LEGACY_TAGS: tuple[str, ...] = ("identity", "identity-document")
NEW_TAG: str = "identity_document"
NEW_ACCESS_ROLES: list[str] = ["self", "admin"]

_URI_RE = re.compile(r"/uploaded/by_id/([^/?#]+)")


def _extract_uuid(uri: str | None) -> str | None:
    if not uri:
        return None
    m = _URI_RE.search(uri)
    return m.group(1) if m else None


def _copy_artifacts(uuid: str, upload_dir: Path) -> int:
    """Copy every ``<uuid>*`` file at the upload_dir root into ``media/``.

    Returns the count of files actually copied (excludes skipped-identical).
    """
    media_dir = upload_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for src in upload_dir.glob(f"{uuid}*"):
        if not src.is_file():
            continue
        dst = media_dir / src.name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        shutil.copy2(src, dst)
        copied += 1
    return copied


def backfill(conn: Connection, upload_dir: Path) -> dict[str, int]:
    """Run the Phase A backfill against ``conn``. Returns per-category counts.

    Categories:
    - ``migrated``: legacy gallery row produced a ``media`` + link row.
    - ``skipped_external_uri``: gallery row's URI is not a ``/uploaded/by_id/<uuid>`` reference.
    - ``skipped_orphan``: gallery row's referenced UUID has no live ``uploaded_media``.
    - ``skipped_deleted``: matching ``uploaded_media`` row is soft-deleted.
    - ``skipped_missing_file``: matching ``uploaded_media`` exists but no on-disk artifacts found.
    - ``files_copied``: total artifact files copied (informational, not row-count).
    """
    counts = {
        "migrated": 0,
        "skipped_external_uri": 0,
        "skipped_orphan": 0,
        "skipped_deleted": 0,
        "skipped_missing_file": 0,
        "files_copied": 0,
    }

    tag_list = ", ".join(f"'{t}'" for t in LEGACY_TAGS)
    rows = (
        conn.execute(
            text(
                f"""
        SELECT g.username, g.tag AS legacy_tag, g.uri,
               g.created_at AS link_created_at,
               g.updated_at AS link_updated_at,
               u.uuid, u.uploaded_by, u.original_filename, u.mime_type,
               u.original_extension, u.file_size, u.media_type,
               u.preserve_original, u.conversion_status,
               u.is_encrypted, u.encryption_version, u.encryption_meta,
               u.created_at AS media_created_at,
               u.updated_at AS media_updated_at,
               u.deleted_at
          FROM user_gallery g
          LEFT JOIN uploaded_media u
            ON u.uuid = substring(g.uri from '/uploaded/by_id/([^/?#]+)')
         WHERE g.tag IN ({tag_list})
        """
            )
        )
        .mappings()
        .all()
    )

    for r in rows:
        uuid_from_uri = _extract_uuid(r["uri"])
        if uuid_from_uri is None:
            counts["skipped_external_uri"] += 1
            logger.info(
                "backfill[170]: skip external uri username=%s uri=%s",
                r["username"],
                r["uri"],
            )
            continue

        if r["uuid"] is None:
            counts["skipped_orphan"] += 1
            logger.info(
                "backfill[170]: skip orphan username=%s uuid=%s",
                r["username"],
                uuid_from_uri,
            )
            continue

        if r["deleted_at"] is not None:
            counts["skipped_deleted"] += 1
            logger.info("backfill[170]: skip soft-deleted uuid=%s", r["uuid"])
            continue

        copied = _copy_artifacts(r["uuid"], upload_dir)
        # If we copied nothing AND nothing pre-existed at the destination,
        # the source file is missing — skip without inserting DB rows.
        media_dst = (upload_dir / "media").glob(f"{r['uuid']}*")
        if copied == 0 and not any(media_dst):
            counts["skipped_missing_file"] += 1
            logger.warning("backfill[170]: missing on-disk file for uuid=%s", r["uuid"])
            continue
        counts["files_copied"] += copied

        conn.execute(
            text(
                """
            INSERT INTO media (
                uuid, uploaded_by, original_filename, mime_type,
                original_extension, file_size, media_type,
                preserve_original, conversion_status,
                access_roles, is_encrypted, encryption_version, encryption_meta,
                created_at, updated_at
            ) VALUES (
                :uuid, :uploaded_by, :original_filename, :mime_type,
                :original_extension, :file_size, :media_type,
                :preserve_original, :conversion_status,
                :access_roles, :is_encrypted, :encryption_version, :encryption_meta,
                :created_at, :updated_at
            )
            ON CONFLICT (uuid) DO NOTHING
            """
            ),
            {
                "uuid": r["uuid"],
                "uploaded_by": r["uploaded_by"],
                "original_filename": r["original_filename"],
                "mime_type": r["mime_type"],
                "original_extension": r["original_extension"],
                "file_size": r["file_size"],
                "media_type": r["media_type"],
                "preserve_original": r["preserve_original"],
                "conversion_status": r["conversion_status"],
                "access_roles": json.dumps(NEW_ACCESS_ROLES),
                "is_encrypted": r["is_encrypted"],
                "encryption_version": r["encryption_version"],
                "encryption_meta": r["encryption_meta"],
                "created_at": r["media_created_at"],
                "updated_at": r["media_updated_at"],
            },
        )

        conn.execute(
            text(
                """
            INSERT INTO user_media (
                username, tag, media_uuid, metadata_value,
                created_at, updated_at
            ) VALUES (
                :username, :tag, :media_uuid, NULL,
                :created_at, :updated_at
            )
            ON CONFLICT DO NOTHING
            """
            ),
            {
                "username": r["username"],
                "tag": NEW_TAG,
                "media_uuid": r["uuid"],
                "created_at": r["link_created_at"],
                "updated_at": r["link_updated_at"],
            },
        )

        counts["migrated"] += 1

    logger.info("backfill[170] summary: %s", counts)
    return counts


def revert(conn: Connection) -> dict[str, int]:
    """Reverse the backfill for ``downgrade()``. On-disk files are left in place."""
    counts = {"links_deleted": 0, "media_deleted": 0}

    # Capture uuids before deleting links (link delete cascades nothing on media).
    uuids = [
        r["media_uuid"]
        for r in conn.execute(
            text("SELECT media_uuid FROM user_media WHERE tag = :tag"), {"tag": NEW_TAG}
        )
        .mappings()
        .all()
    ]

    res = conn.execute(
        text("DELETE FROM user_media WHERE tag = :tag"), {"tag": NEW_TAG}
    )
    counts["links_deleted"] = res.rowcount or 0

    if uuids:
        res2 = conn.execute(
            text("DELETE FROM media WHERE uuid = ANY(:uuids)"), {"uuids": uuids}
        )
        counts["media_deleted"] = res2.rowcount or 0

    logger.info("backfill[170] revert summary: %s", counts)
    return counts
