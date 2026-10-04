"""Unit tests for the v2 media video conversion worker (#190).

Mocks asyncio.create_subprocess_exec to drive the worker logic against
the real test database. Mirrors the v1 tests/test_worker.py deleted in
#182 but keyed on the ``media`` table.
"""

import asyncio
import json
import shutil
import uuid as uuid_mod
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from club_server import worker as worker_mod
from club_server.config import settings
from club_server.db.models.media import Media
from club_server.db.models.user import User, UserStatus
from club_server.utils import now_utc_ms


def _pending_dir() -> Path:
    return Path(settings.upload_dir) / "media" / "_pending"


def _media_dir() -> Path:
    return Path(settings.upload_dir) / "media"


async def seed_video(
    session: AsyncSession,
    *,
    conversion_status: str = "pending",
    deleted_at: int | None = None,
    extension: str = "mp4",
    preserve_original: bool = False,
    create_staged_input: bool = False,
) -> Media:
    """Insert a video Media row and optionally stage a fake input file."""
    uid = str(uuid_mod.uuid4())
    media = Media(
        uuid=uid,
        original_filename=f"test.{extension}",
        media_type="video",
        mime_type="video/mp4",
        original_mime_type="video/mp4",
        original_extension=extension,
        file_size=1024,
        preserve_original=1 if preserve_original else 0,
        conversion_status=conversion_status,
        conversion_params=json.dumps({"duration": 3, "start": 0}),
        uploaded_by="testuser",
        access_roles='["public"]',
        created_at=now_utc_ms(),
        updated_at=now_utc_ms(),
        deleted_at=deleted_at,
    )
    session.add(media)
    await session.flush()

    if create_staged_input:
        pending = _pending_dir()
        pending.mkdir(parents=True, exist_ok=True)
        (pending / f"{uid}_input.{extension}").write_bytes(b"fake video bytes")

    return media


async def fetch_media(test_engine, media_id: int) -> Media:
    """Read a media row from a fresh session to see committed worker writes."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        result = await session.execute(select(Media).where(Media.id == media_id))
        return result.scalar_one()


def _output_prefix(args) -> str:
    args_list = list(args)
    output_file = args_list[args_list.index("--output") + 1]
    return str(Path(output_file).with_suffix(""))


def make_success_side_effect():
    """Subprocess mock that materializes the expected output files."""

    async def side_effect(*args, **_kwargs):
        prefix = _output_prefix(args)
        Path(f"{prefix}.mp4").write_bytes(b"fake mp4")
        Path(f"{prefix}_poster.webp").write_bytes(b"fake poster")
        Path(f"{prefix}_animated.webp").write_bytes(b"fake animated")
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    return side_effect


def make_failure_side_effect(stderr: bytes = b"ffmpeg blew up"):
    async def side_effect(*_args, **_kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", stderr))
        return proc

    return side_effect


@pytest_asyncio.fixture
async def worker_env(test_engine, db_session):
    """Reset worker module state, ensure upload dir exists, seed FK user."""
    db_session.add(
        User(
            username="testuser",
            password="hashed",
            first_name="Test",
            status=UserStatus.active.value,
            is_super_admin=0,
            roles=json.dumps({"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await db_session.commit()

    media_dir = _media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    _pending_dir().mkdir(parents=True, exist_ok=True)

    old_scheduler = worker_mod._scheduler_task
    old_video = worker_mod._video_task
    old_maker = worker_mod._session_maker
    worker_mod._scheduler_task = None
    worker_mod._video_task = None
    worker_mod._session_maker = None

    yield db_session

    for task in (worker_mod._video_task, worker_mod._scheduler_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    worker_mod._scheduler_task = old_scheduler
    worker_mod._video_task = old_video
    worker_mod._session_maker = old_maker

    shutil.rmtree(media_dir, ignore_errors=True)


@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
@pytest.mark.requirement("media:R16")
async def test_successful_conversion_writes_variants_and_marks_completed(
    mock_exec,
    worker_env,
    test_engine,
):
    """Happy path: subprocess succeeds, variants land in media dir, status=completed."""
    db = worker_env
    m = await seed_video(db, create_staged_input=True)
    await db.commit()

    mock_exec.side_effect = make_success_side_effect()

    await worker_mod._run_conversion(m.id)

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "completed"
    assert refreshed.conversion_error is None
    assert (_media_dir() / f"{m.uuid}.mp4").exists()
    assert (_media_dir() / f"{m.uuid}_poster.webp").exists()
    assert (_media_dir() / f"{m.uuid}_animated.webp").exists()
    # Staged input has been consumed.
    assert not (_pending_dir() / f"{m.uuid}_input.mp4").exists()


@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
@pytest.mark.requirement("media:R16")
async def test_preserve_original_keeps_source_and_drops_mp4(
    mock_exec,
    worker_env,
    test_engine,
):
    """preserve_original=True keeps the source extension and discards converted mp4."""
    db = worker_env
    m = await seed_video(
        db,
        extension="mov",
        preserve_original=True,
        create_staged_input=True,
    )
    await db.commit()

    mock_exec.side_effect = make_success_side_effect()

    await worker_mod._run_conversion(m.id)

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "completed"
    # Original kept under original extension.
    assert (_media_dir() / f"{m.uuid}.mov").exists()
    # Converted mp4 should NOT be retained when preserving original.
    assert not (_media_dir() / f"{m.uuid}.mp4").exists()
    # Poster + animated still produced.
    assert (_media_dir() / f"{m.uuid}_poster.webp").exists()
    assert (_media_dir() / f"{m.uuid}_animated.webp").exists()


@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
@pytest.mark.requirement("media:R17")
async def test_subprocess_failure_marks_failed_with_stderr(
    mock_exec,
    worker_env,
    test_engine,
):
    db = worker_env
    m = await seed_video(db, create_staged_input=True)
    await db.commit()

    mock_exec.side_effect = make_failure_side_effect(b"codec not found")

    await worker_mod._run_conversion(m.id)

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "failed"
    assert "codec not found" in refreshed.conversion_error


@pytest.mark.asyncio
@pytest.mark.requirement("media:R17")
async def test_missing_staged_input_marks_failed(worker_env, test_engine):
    db = worker_env
    m = await seed_video(db, create_staged_input=False)
    await db.commit()

    with patch("club_server.worker.asyncio.create_subprocess_exec") as mock_exec:
        await worker_mod._run_conversion(m.id)
        mock_exec.assert_not_called()

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "failed"
    assert refreshed.conversion_error == "Staged input file not found"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R18")
async def test_deleted_row_is_skipped(worker_env, test_engine):
    db = worker_env
    m = await seed_video(db, deleted_at=now_utc_ms(), create_staged_input=True)
    await db.commit()

    with patch("club_server.worker.asyncio.create_subprocess_exec") as mock_exec:
        await worker_mod._run_conversion(m.id)
        mock_exec.assert_not_called()

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "pending"


@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
@pytest.mark.requirement("media:R18")
async def test_already_completed_row_is_skipped(
    mock_exec,
    worker_env,
    test_engine,
):
    db = worker_env
    m = await seed_video(
        db,
        conversion_status="completed",
        create_staged_input=False,
    )
    await db.commit()

    await worker_mod._run_conversion(m.id)
    mock_exec.assert_not_called()

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "completed"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R19")
async def test_recover_stuck_jobs_resets_processing_to_pending(
    worker_env,
    test_engine,
):
    db = worker_env
    m1 = await seed_video(db, conversion_status="processing")
    m2 = await seed_video(db, conversion_status="processing")
    m3 = await seed_video(db, conversion_status="pending")
    m4 = await seed_video(
        db,
        conversion_status="processing",
        deleted_at=now_utc_ms(),
    )
    await db.commit()

    await worker_mod._recover_stuck_jobs()

    assert (await fetch_media(test_engine, m1.id)).conversion_status == "pending"
    assert (await fetch_media(test_engine, m2.id)).conversion_status == "pending"
    assert (await fetch_media(test_engine, m3.id)).conversion_status == "pending"
    # Deleted rows are not touched.
    assert (await fetch_media(test_engine, m4.id)).conversion_status == "processing"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R15")
async def test_claim_next_pending_marks_processing(worker_env, test_engine):
    db = worker_env
    m = await seed_video(db, create_staged_input=True)
    await db.commit()

    claimed = await worker_mod._claim_next_pending_video()
    assert claimed == m.id

    refreshed = await fetch_media(test_engine, m.id)
    assert refreshed.conversion_status == "processing"

    # Second claim finds nothing.
    assert await worker_mod._claim_next_pending_video() is None


@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
@pytest.mark.requirement("media:R15")
async def test_loop_processes_pending_then_idles(
    mock_exec,
    worker_env,
    test_engine,
):
    """Drive the loop directly: a pending row completes, then the loop idles."""
    db = worker_env
    m = await seed_video(db, create_staged_input=True)
    await db.commit()

    mock_exec.side_effect = make_success_side_effect()

    # Shorten the idle sleep so the test doesn't drag.
    with patch.object(worker_mod, "VIDEO_POLL_INTERVAL_SECONDS", 0.05):
        task = asyncio.create_task(worker_mod._video_worker_loop())
        try:
            for _ in range(50):
                refreshed = await fetch_media(test_engine, m.id)
                if refreshed.conversion_status == "completed":
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Worker did not complete the pending job in time")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
