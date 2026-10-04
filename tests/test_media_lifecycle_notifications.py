"""Tests for media lifecycle notifications emitted by the video worker (#55).

Verifies the worker calls NotificationService.notify_for_event with the
contract documented in #55: media.processed on success,
media.failed with a machine-readable reasonCode on each failure branch.
"""

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
from club_server.db.models.notification import Notification
from club_server.db.models.user import User, UserStatus
from club_server.utils import now_utc_ms


def _pending_dir() -> Path:
    return Path(settings.upload_dir) / "media" / "_pending"


def _media_dir() -> Path:
    return Path(settings.upload_dir) / "media"


async def _seed_user(session: AsyncSession, username: str) -> None:
    session.add(
        User(
            username=username,
            password="x",
            first_name=username.title(),
            status=UserStatus.active.value,
            is_super_admin=0,
            roles=json.dumps({"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await session.flush()


async def _seed_video(
    session: AsyncSession,
    *,
    uploaded_by: str | None = "uploader",
    create_staged_input: bool = False,
) -> Media:
    uid = str(uuid_mod.uuid4())
    media = Media(
        uuid=uid,
        original_filename="test.mp4",
        media_type="video",
        mime_type="video/mp4",
        original_mime_type="video/mp4",
        original_extension="mp4",
        file_size=1024,
        preserve_original=0,
        conversion_status="pending",
        conversion_params=json.dumps({"duration": 3, "start": 0}),
        uploaded_by=uploaded_by,
        access_roles='["public"]',
        created_at=now_utc_ms(),
        updated_at=now_utc_ms(),
    )
    session.add(media)
    await session.flush()
    if create_staged_input:
        pending = _pending_dir()
        pending.mkdir(parents=True, exist_ok=True)
        (pending / f"{uid}_input.mp4").write_bytes(b"fake")
    return media


async def _notifications_for(test_engine, username: str) -> list[Notification]:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        res = await s.execute(
            select(Notification).where(Notification.username == username),
        )
        return list(res.scalars().all())


def _success_subprocess():
    """Subprocess mock that materializes the expected output files."""

    async def side_effect(*args, **_kwargs):
        args_list = list(args)
        output = args_list[args_list.index("--output") + 1]
        prefix = str(Path(output).with_suffix(""))
        Path(f"{prefix}.mp4").write_bytes(b"fake mp4")
        Path(f"{prefix}_poster.webp").write_bytes(b"fake poster")
        Path(f"{prefix}_animated.webp").write_bytes(b"fake animated")
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    return side_effect


def _failure_subprocess(stderr: bytes = b"boom"):
    async def side_effect(*_args, **_kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", stderr))
        return proc

    return side_effect


@pytest_asyncio.fixture
async def env(test_engine, db_session):
    await _seed_user(db_session, "uploader")
    await db_session.commit()
    _media_dir().mkdir(parents=True, exist_ok=True)
    _pending_dir().mkdir(parents=True, exist_ok=True)
    worker_mod._session_maker = None
    yield db_session
    shutil.rmtree(_media_dir(), ignore_errors=True)


@pytest.mark.requirement("notifications:R108")
@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
async def test_successful_conversion_emits_media_processed(
    mock_exec,
    env,
    test_engine,
):
    """Happy path emits exactly one media.processed for the uploader."""
    m = await _seed_video(env, create_staged_input=True)
    await env.commit()
    mock_exec.side_effect = _success_subprocess()

    await worker_mod._run_conversion(m.id)

    rows = await _notifications_for(test_engine, "uploader")
    assert len(rows) == 1
    n = rows[0]
    assert n.type == "media.processed"
    assert n.payload["v"] == 1
    assert n.payload["type"] == "media.processed"
    assert n.payload["data"] == {"mediaUuid": m.uuid, "kind": "video"}
    assert "reasonCode" not in n.payload["data"]


@pytest.mark.requirement("notifications:R108")
@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
async def test_transcode_failure_emits_media_failed_with_reason(
    mock_exec,
    env,
    test_engine,
):
    """Subprocess non-zero exit emits media.failed with reasonCode=TRANSCODE_FAILED."""
    m = await _seed_video(env, create_staged_input=True)
    await env.commit()
    mock_exec.side_effect = _failure_subprocess(b"codec not found")

    await worker_mod._run_conversion(m.id)

    rows = await _notifications_for(test_engine, "uploader")
    assert len(rows) == 1
    n = rows[0]
    assert n.type == "media.failed"
    assert n.payload["data"] == {
        "mediaUuid": m.uuid,
        "kind": "video",
        "reasonCode": "TRANSCODE_FAILED",
    }


@pytest.mark.requirement("notifications:R108")
@pytest.mark.asyncio
async def test_missing_staged_input_emits_media_failed_with_reason(env, test_engine):
    """Missing staged input emits reasonCode=STAGED_INPUT_MISSING."""
    m = await _seed_video(env, create_staged_input=False)
    await env.commit()

    with patch("club_server.worker.asyncio.create_subprocess_exec") as mock_exec:
        await worker_mod._run_conversion(m.id)
        mock_exec.assert_not_called()

    rows = await _notifications_for(test_engine, "uploader")
    assert len(rows) == 1
    assert rows[0].type == "media.failed"
    assert rows[0].payload["data"]["reasonCode"] == "STAGED_INPUT_MISSING"


@pytest.mark.requirement("notifications:R108")
@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
async def test_unexpected_error_emits_media_failed_with_reason(
    mock_exec,
    env,
    test_engine,
):
    """Any uncaught exception during conversion emits reasonCode=UNEXPECTED_ERROR."""
    m = await _seed_video(env, create_staged_input=True)
    await env.commit()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    mock_exec.side_effect = boom

    await worker_mod._run_conversion(m.id)

    rows = await _notifications_for(test_engine, "uploader")
    assert len(rows) == 1
    assert rows[0].type == "media.failed"
    assert rows[0].payload["data"]["reasonCode"] == "UNEXPECTED_ERROR"


@pytest.mark.requirement("notifications:R108")
@pytest.mark.asyncio
@patch("club_server.worker.asyncio.create_subprocess_exec")
async def test_no_recipient_when_uploader_is_null(
    mock_exec,
    env,
    test_engine,
):
    """Uploads outlive uploaders (#157) — a null uploaded_by must not emit."""
    m = await _seed_video(env, uploaded_by=None, create_staged_input=True)
    await env.commit()
    mock_exec.side_effect = _success_subprocess()

    await worker_mod._run_conversion(m.id)

    # No user to notify; no notifications should land anywhere.
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        res = await s.execute(select(Notification))
        assert list(res.scalars().all()) == []
