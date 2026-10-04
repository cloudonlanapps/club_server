"""Tests for #173 — Phase B identity-document cleanup helper."""

import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.identity_documents_cleanup import (
    LEGACY_USAGE_CONTEXT,
    PhaseAIncompleteError,
    cleanup,
)

from .helpers import create_registered_user


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    d = tmp_path / "uploads"
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest_asyncio.fixture(autouse=True)
async def legacy_tables(db_session: AsyncSession):
    """Re-materialize the legacy ``user_gallery`` and ``uploaded_media``
    tables for the duration of one test. Both are dropped at HEAD (by
    ``y9z0a1b2c3d4`` and the #182 drop migration respectively) and
    their SQLAlchemy models are gone, but the cleanup function still
    issues DELETEs against them (production ordering: cleanup migration
    runs before either drop)."""
    await db_session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS user_gallery (
            _id        serial PRIMARY KEY,
            id         uuid NOT NULL DEFAULT gen_random_uuid(),
            username   varchar(50) NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            tag        varchar(64) NOT NULL,
            uri        text NOT NULL,
            created_at bigint NOT NULL,
            updated_at bigint NOT NULL
        )
        """
        )
    )
    await db_session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS uploaded_media (
            id                  serial PRIMARY KEY,
            uuid                varchar(36) NOT NULL UNIQUE,
            uploaded_by         varchar(50),
            original_filename   text NOT NULL,
            mime_type           varchar(128) NOT NULL,
            original_extension  varchar(16) NOT NULL,
            file_size           bigint NOT NULL,
            media_type          varchar(16) NOT NULL,
            preserve_original   smallint NOT NULL DEFAULT 0,
            conversion_status   varchar(16) NOT NULL,
            conversion_params   text,
            conversion_error    text,
            access_roles        text NOT NULL,
            is_encrypted        smallint NOT NULL DEFAULT 0,
            encryption_version  smallint,
            encryption_meta     text,
            usage_context       varchar(64),
            created_at          bigint NOT NULL,
            updated_at          bigint NOT NULL,
            deleted_at          bigint
        )
        """
        )
    )
    yield
    await db_session.execute(text("DROP TABLE IF EXISTS uploaded_media"))
    await db_session.execute(text("DROP TABLE IF EXISTS user_gallery"))


async def _insert_uploaded_media(
    db: AsyncSession,
    *,
    uuid: str,
    uploaded_by: str,
    usage_context: str | None = LEGACY_USAGE_CONTEXT,
    deleted_at: int | None = None,
) -> None:
    await db.execute(
        text(
            """
        INSERT INTO uploaded_media (
            uuid, uploaded_by, original_filename, mime_type, original_extension,
            file_size, media_type, preserve_original, conversion_status,
            access_roles, is_encrypted, usage_context,
            created_at, updated_at, deleted_at
        ) VALUES (
            :uuid, :uploaded_by, 'id.png', 'image/png', 'webp',
            100, 'image', 0, 'completed',
            '["self","admin"]', 0, :usage_context,
            1000, 1500, :deleted_at
        )
        """
        ),
        {
            "uuid": uuid,
            "uploaded_by": uploaded_by,
            "usage_context": usage_context,
            "deleted_at": deleted_at,
        },
    )


async def _insert_media(
    db: AsyncSession,
    *,
    uuid: str,
    uploaded_by: str,
) -> None:
    await db.execute(
        text(
            """
        INSERT INTO media (
            uuid, uploaded_by, original_filename, mime_type, original_mime_type, original_extension,
            file_size, media_type, preserve_original, conversion_status,
            access_roles, is_encrypted, created_at, updated_at
        ) VALUES (
            :uuid, :uploaded_by, 'id.png', 'image/webp', 'image/png', 'webp',
            100, 'image', 0, 'completed',
            '["self","admin"]', 0, 1000, 1500
        )
        """
        ),
        {"uuid": uuid, "uploaded_by": uploaded_by},
    )


async def _insert_user_gallery(
    db: AsyncSession,
    *,
    username: str,
    tag: str,
    uri: str,
) -> None:
    await db.execute(
        text(
            """
        INSERT INTO user_gallery (id, username, tag, uri, created_at, updated_at)
        VALUES (gen_random_uuid(), :username, :tag, :uri, 2000, 2500)
        """
        ),
        {"username": username, "tag": tag, "uri": uri},
    )


def _write(upload_dir: Path, name: str, size: int = 100) -> Path:
    p = upload_dir / name
    p.write_bytes(b"x" * size)
    return p


async def _run_cleanup(db: AsyncSession, upload_dir: Path) -> dict[str, int]:
    raw = await db.connection()
    return await raw.run_sync(lambda sync_conn: cleanup(sync_conn, upload_dir))


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_cleanup_removes_legacy_rows_and_root_files(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    uid = "id-uuid-alice"
    await _insert_uploaded_media(db_session, uuid=uid, uploaded_by="alice")
    await _insert_media(db_session, uuid=uid, uploaded_by="alice")
    await _insert_user_gallery(
        db_session,
        username="alice",
        tag="identity",
        uri=f"http://api/v1/uploaded/by_id/{uid}/download",
    )
    legacy_file = _write(upload_dir, f"{uid}.webp")
    media_dir = upload_dir / "media"
    media_dir.mkdir()
    canonical_file = media_dir / f"{uid}.webp"
    canonical_file.write_bytes(b"canonical")
    await db_session.flush()

    counts = await _run_cleanup(db_session, upload_dir)
    assert counts["gallery_rows_deleted"] == 1
    assert counts["uploaded_media_rows_deleted"] == 1
    assert counts["uuids_processed"] == 1
    assert counts["files_deleted"] == 1

    assert not legacy_file.exists()
    # Canonical media/ copy is untouched.
    assert canonical_file.read_bytes() == b"canonical"

    # DB rows gone.
    gal = (
        await db_session.execute(text("SELECT COUNT(*) AS c FROM user_gallery"))
    ).scalar_one()
    assert gal == 0
    um = (
        await db_session.execute(text("SELECT COUNT(*) AS c FROM uploaded_media"))
    ).scalar_one()
    assert um == 0
    # New stack survives.
    md = (
        await db_session.execute(
            text("SELECT COUNT(*) AS c FROM media WHERE uuid = :u"), {"u": uid}
        )
    ).scalar_one()
    assert md == 1


@pytest.mark.asyncio
async def test_cleanup_handles_both_tag_spellings(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    await _insert_user_gallery(
        db_session,
        username="alice",
        tag="identity",
        uri="http://x/1",
    )
    await _insert_user_gallery(
        db_session,
        username="alice",
        tag="identity-document",
        uri="http://x/2",
    )
    await db_session.flush()

    counts = await _run_cleanup(db_session, upload_dir)
    assert counts["gallery_rows_deleted"] == 2


@pytest.mark.asyncio
async def test_cleanup_leaves_other_uploaded_media_alone(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    keep = "keep-uuid"
    await _insert_uploaded_media(
        db_session,
        uuid=keep,
        uploaded_by="alice",
        usage_context=None,
    )
    _write(upload_dir, f"{keep}.webp")
    await db_session.flush()

    counts = await _run_cleanup(db_session, upload_dir)
    assert counts["uploaded_media_rows_deleted"] == 0
    assert (upload_dir / f"{keep}.webp").exists()


@pytest.mark.asyncio
async def test_cleanup_idempotent_on_clean_db(
    db_session: AsyncSession,
    upload_dir: Path,
):
    counts = await _run_cleanup(db_session, upload_dir)
    assert counts == {
        "gallery_rows_deleted": 0,
        "uploaded_media_rows_deleted": 0,
        "uuids_processed": 0,
        "files_deleted": 0,
    }


@pytest.mark.asyncio
async def test_cleanup_leaves_non_identity_gallery_rows(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    await _insert_user_gallery(
        db_session,
        username="alice",
        tag="avatar",
        uri="http://x/avatar",
    )
    await db_session.flush()
    await _run_cleanup(db_session, upload_dir)
    survivors = (
        await db_session.execute(
            text("SELECT COUNT(*) AS c FROM user_gallery WHERE tag = 'avatar'")
        )
    ).scalar_one()
    assert survivors == 1


# ---------- safety pre-check ----------


@pytest.mark.asyncio
async def test_cleanup_aborts_when_phase_a_incomplete(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    uid = "orphan-uuid"
    await _insert_uploaded_media(db_session, uuid=uid, uploaded_by="alice")
    # NB: no matching ``media`` row — Phase A never copied this uuid.
    _write(upload_dir, f"{uid}.webp")
    await db_session.flush()

    with pytest.raises(PhaseAIncompleteError):
        await _run_cleanup(db_session, upload_dir)

    # Nothing was deleted.
    assert (upload_dir / f"{uid}.webp").exists()
    survivors = (
        await db_session.execute(
            text("SELECT COUNT(*) AS c FROM uploaded_media WHERE uuid = :u"), {"u": uid}
        )
    ).scalar_one()
    assert survivors == 1


# ---------- on-disk sweep ----------


@pytest.mark.asyncio
async def test_cleanup_removes_all_uuid_prefixed_root_files(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    uid = "id-uuid-sweep"
    await _insert_uploaded_media(db_session, uuid=uid, uploaded_by="alice")
    await _insert_media(db_session, uuid=uid, uploaded_by="alice")
    _write(upload_dir, f"{uid}.webp")
    _write(upload_dir, f"{uid}_poster.webp")
    _write(upload_dir, f"{uid}.webp.enc")
    # An unrelated file must survive.
    other = _write(upload_dir, "other.webp")
    await db_session.flush()

    counts = await _run_cleanup(db_session, upload_dir)
    assert counts["files_deleted"] == 3
    assert other.exists()
    for name in (f"{uid}.webp", f"{uid}_poster.webp", f"{uid}.webp.enc"):
        assert not (upload_dir / name).exists()
