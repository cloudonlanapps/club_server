"""Tests for #182 — terminal legacy ``uploaded_media`` cleanup helper."""

import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.uploaded_media_cleanup import (
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
async def legacy_uploaded_media_table(db_session: AsyncSession):
    """Re-materialize the legacy ``uploaded_media`` table for one test.
    The table is dropped at HEAD by the #182 drop migration and its
    SQLAlchemy model is gone, but the cleanup function still issues a
    DELETE against it (production ordering: cleanup migration runs
    before the drop)."""
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
            access_roles        text NOT NULL,
            is_encrypted        smallint NOT NULL DEFAULT 0,
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


async def _insert_uploaded_media(
    db: AsyncSession,
    *,
    uuid: str,
    uploaded_by: str,
) -> None:
    await db.execute(
        text(
            """
        INSERT INTO uploaded_media (
            uuid, uploaded_by, original_filename, mime_type,
            original_extension, file_size, media_type, preserve_original,
            conversion_status, access_roles, is_encrypted,
            created_at, updated_at
        ) VALUES (
            :uuid, :uploaded_by, 'file.png', 'image/png', 'webp',
            100, 'image', 0, 'completed', '["public"]', 0, 1000, 1500
        )
        """
        ),
        {"uuid": uuid, "uploaded_by": uploaded_by},
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
            uuid, uploaded_by, original_filename, mime_type, original_mime_type,
            original_extension, file_size, media_type, preserve_original,
            conversion_status, access_roles, is_encrypted,
            created_at, updated_at
        ) VALUES (
            :uuid, :uploaded_by, 'file.png', 'image/webp', 'image/png', 'webp',
            100, 'image', 0, 'completed', '["public"]', 0, 1000, 1500
        )
        """
        ),
        {"uuid": uuid, "uploaded_by": uploaded_by},
    )


def _write(upload_dir: Path, name: str) -> Path:
    p = upload_dir / name
    p.write_bytes(b"x" * 50)
    return p


async def _run_cleanup(db: AsyncSession, upload_dir: Path) -> dict[str, int]:
    raw = await db.connection()
    return await raw.run_sync(lambda sync_conn: cleanup(sync_conn, upload_dir))


@pytest.mark.asyncio
@pytest.mark.requirement("media:R107")
async def test_cleanup_removes_remaining_rows_and_root_files(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    uid = "remaining-1"
    await _insert_uploaded_media(db_session, uuid=uid, uploaded_by="alice")
    await _insert_media(db_session, uuid=uid, uploaded_by="alice")
    legacy = _write(upload_dir, f"{uid}.webp")
    media_dir = upload_dir / "media"
    media_dir.mkdir()
    canonical = media_dir / f"{uid}.webp"
    canonical.write_bytes(b"canonical")
    await db_session.flush()

    counts = await _run_cleanup(db_session, upload_dir)
    assert counts["uploaded_media_rows_deleted"] == 1
    assert counts["uuids_processed"] == 1
    assert counts["files_deleted"] == 1
    assert not legacy.exists()
    assert canonical.read_bytes() == b"canonical"

    n = (
        await db_session.execute(text("SELECT COUNT(*) AS c FROM uploaded_media"))
    ).scalar_one()
    assert n == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R107")
async def test_cleanup_idempotent_on_clean_db(
    db_session: AsyncSession,
    upload_dir: Path,
):
    counts = await _run_cleanup(db_session, upload_dir)
    assert counts == {
        "uploaded_media_rows_deleted": 0,
        "uuids_processed": 0,
        "files_deleted": 0,
    }


@pytest.mark.asyncio
@pytest.mark.requirement("media:R107")
async def test_cleanup_aborts_when_phase_a_incomplete(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    uid = "orphan-uuid"
    await _insert_uploaded_media(db_session, uuid=uid, uploaded_by="alice")
    # NB: no matching media row.
    _write(upload_dir, f"{uid}.webp")
    await db_session.flush()

    with pytest.raises(PhaseAIncompleteError):
        await _run_cleanup(db_session, upload_dir)

    assert (upload_dir / f"{uid}.webp").exists()
    n = (
        await db_session.execute(
            text("SELECT COUNT(*) AS c FROM uploaded_media WHERE uuid = :u"), {"u": uid}
        )
    ).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_cleanup_sweeps_all_uuid_prefixed_root_files(
    db_session: AsyncSession,
    upload_dir: Path,
):
    await create_registered_user(db_session, "alice")
    uid = "sweep-uuid"
    await _insert_uploaded_media(db_session, uuid=uid, uploaded_by="alice")
    await _insert_media(db_session, uuid=uid, uploaded_by="alice")
    _write(upload_dir, f"{uid}.mp4")
    _write(upload_dir, f"{uid}_poster.webp")
    _write(upload_dir, f"{uid}_animated.webp")
    other = _write(upload_dir, "unrelated.webp")
    await db_session.flush()

    counts = await _run_cleanup(db_session, upload_dir)
    assert counts["files_deleted"] == 3
    assert other.exists()
    for name in (f"{uid}.mp4", f"{uid}_poster.webp", f"{uid}_animated.webp"):
        assert not (upload_dir / name).exists()
