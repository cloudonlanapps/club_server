"""Background workers started/stopped via app lifespan.

Hosts the scheduler loop (time-triggered notifications) and the video
conversion worker for v2 ``/v1/media`` uploads (#190). The video worker
polls the ``media`` table for ``media_type='video' AND
conversion_status='pending'`` rows, runs ``scripts/media_convert.sh`` to
produce the ``.mp4`` / ``_poster.webp`` / ``_animated.webp`` artifacts,
and updates the row.
"""

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .db.models.media import Media
from .services.media_pipeline import get_script_path
from .services.notification import NotificationEvent, NotificationService
from .services.scheduler import scheduler_loop
from .utils import now_utc_ms

logger = logging.getLogger(__name__)

VIDEO_POLL_INTERVAL_SECONDS = 5

_scheduler_task: asyncio.Task | None = None
_video_task: asyncio.Task | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None
_stop_event: asyncio.Event | None = None


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Get or create a session maker for the workers' own event loop."""
    global _session_maker
    if _session_maker is None:
        engine = create_async_engine(settings.database_url, echo=False)
        _session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_maker


async def start_worker() -> None:
    """Start background workers. Called from app lifespan."""
    global _scheduler_task, _video_task, _stop_event
    await _recover_stuck_jobs()
    _stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(scheduler_loop(_get_session_maker()))
    _video_task = asyncio.create_task(_video_worker_loop())
    logger.info("Scheduler + video worker started")


async def stop_worker() -> None:
    """Gracefully stop background workers. Called from app lifespan shutdown."""
    global _scheduler_task, _video_task, _session_maker, _stop_event
    if _stop_event:
        _stop_event.set()
    for task_name, task in [("scheduler", _scheduler_task), ("video", _video_task)]:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("%s task stopped", task_name)
    _scheduler_task = None
    _video_task = None

    # Reset any jobs left in 'processing' back to 'pending' so the next
    # process can pick them up.
    async with _get_session_maker()() as session:
        result = await session.execute(
            select(Media).where(
                Media.media_type == "video",
                Media.conversion_status == "processing",
                Media.deleted_at.is_(None),
            ),
        )
        for media in result.scalars().all():
            media.conversion_status = "pending"
            media.updated_at = now_utc_ms()
        await session.commit()

    _session_maker = None
    _stop_event = None


async def _recover_stuck_jobs() -> None:
    """Reset 'processing' video rows from a prior crash to 'pending'."""
    async with _get_session_maker()() as session:
        result = await session.execute(
            select(Media).where(
                Media.media_type == "video",
                Media.conversion_status == "processing",
                Media.deleted_at.is_(None),
            ),
        )
        rows = result.scalars().all()
        for media in rows:
            media.conversion_status = "pending"
            media.updated_at = now_utc_ms()
        if rows:
            await session.commit()
            logger.info("Recovered %d stuck video conversion jobs", len(rows))


async def _video_worker_loop() -> None:
    """Poll for pending video rows and process them one at a time."""
    while True:
        try:
            media_id = await _claim_next_pending_video()
            if media_id is None:
                await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)
                continue
            await _run_conversion(media_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in video worker loop")
            await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)


async def _claim_next_pending_video() -> int | None:
    """Atomically pick the oldest pending video row and mark it 'processing'.

    Returns the media id, or None if no pending rows exist.
    """
    async with _get_session_maker()() as session:
        result = await session.execute(
            select(Media)
            .where(
                Media.media_type == "video",
                Media.conversion_status == "pending",
                Media.deleted_at.is_(None),
            )
            .order_by(Media.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True),
        )
        media = result.scalar_one_or_none()
        if media is None:
            return None
        media.conversion_status = "processing"
        media.updated_at = now_utc_ms()
        await session.commit()
        return media.id


async def _emit_lifecycle_event(
    session: AsyncSession,
    media: Media,
    event_type: str,
    reason_code: str | None = None,
) -> None:
    """Notify the uploader that conversion finished (#55).

    Skips silently when ``uploaded_by`` is null — uploads outlive their
    uploader (#157), so a deleted user has no recipient.
    """
    if not media.uploaded_by:
        return
    data: dict[str, str] = {"mediaUuid": media.uuid, "kind": media.media_type}
    if reason_code is not None:
        data["reasonCode"] = reason_code
    await NotificationService(session).notify_for_event(
        NotificationEvent(
            type=event_type,
            recipients=[media.uploaded_by],
            data=data,
        )
    )


async def _run_conversion(media_id: int) -> None:
    """Execute video conversion for a single media row."""
    async with _get_session_maker()() as session:
        media = (
            await session.execute(
                select(Media).where(Media.id == media_id),
            )
        ).scalar_one_or_none()
        if not media or media.deleted_at is not None:
            logger.warning("Video media %d not found or deleted, skipping", media_id)
            return

        if media.conversion_status not in ("pending", "processing"):
            logger.info(
                "Video media %d status is %s, skipping",
                media_id,
                media.conversion_status,
            )
            return

        uid = media.uuid
        extension = media.original_extension
        preserve_original = bool(media.preserve_original)
        upload_dir = Path(settings.upload_dir) / "media"
        pending_dir = upload_dir / "_pending"
        input_path = pending_dir / f"{uid}_input.{extension}"

        if not input_path.exists():
            media.conversion_status = "failed"
            media.conversion_error = "Staged input file not found"
            media.updated_at = now_utc_ms()
            await _emit_lifecycle_event(
                session,
                media,
                "media.failed",
                reason_code="STAGED_INPUT_MISSING",
            )
            await session.commit()
            logger.error(
                "Video media %d: staged input missing at %s", media_id, input_path
            )
            return

        duration = 6
        start_time = 0
        if media.conversion_params:
            params = json.loads(media.conversion_params)
            if params.get("duration") is not None:
                duration = params["duration"]
            if params.get("start") is not None:
                start_time = params["start"]

        tmp_dir = Path(tempfile.mkdtemp(prefix="club_video_"))
        try:
            script = get_script_path()
            output_file = str(tmp_dir / f"{uid}.mp4")
            cmd = [
                "bash",
                script,
                "--input",
                str(input_path),
                "--output",
                output_file,
                "--duration",
                str(duration),
                "--start",
                str(start_time),
                "--force",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                media.conversion_status = "failed"
                media.conversion_error = error_msg[:1000]
                media.updated_at = now_utc_ms()
                await _emit_lifecycle_event(
                    session,
                    media,
                    "media.failed",
                    reason_code="TRANSCODE_FAILED",
                )
                await session.commit()
                logger.error(
                    "Conversion failed for media %d: %s",
                    media_id,
                    error_msg,
                )
                return

            upload_dir.mkdir(parents=True, exist_ok=True)
            converted_mp4 = tmp_dir / f"{uid}.mp4"
            poster_src = tmp_dir / f"{uid}_poster.webp"
            animated_src = tmp_dir / f"{uid}_animated.webp"

            if preserve_original:
                # Keep the original file under its source extension; discard the
                # transcoded mp4 since the "original" variant resolves to
                # <uid>.<original_extension>.
                shutil.move(str(input_path), str(upload_dir / f"{uid}.{extension}"))
                if converted_mp4.exists():
                    converted_mp4.unlink()
            else:
                # Replace the original with the transcoded mp4.
                dest_mp4 = upload_dir / f"{uid}.mp4"
                shutil.move(str(converted_mp4), str(dest_mp4))
                media.file_size = dest_mp4.stat().st_size
                # Original input no longer needed.
                input_path.unlink(missing_ok=True)

            if poster_src.exists():
                shutil.move(str(poster_src), str(upload_dir / f"{uid}_poster.webp"))
            if animated_src.exists():
                shutil.move(
                    str(animated_src),
                    str(upload_dir / f"{uid}_animated.webp"),
                )

            media.conversion_status = "completed"
            media.conversion_error = None
            media.updated_at = now_utc_ms()
            await _emit_lifecycle_event(session, media, "media.processed")
            await session.commit()
            logger.info("Conversion completed for media %d (uuid=%s)", media_id, uid)

        except Exception as exc:
            media.conversion_status = "failed"
            media.conversion_error = str(exc)[:1000]
            media.updated_at = now_utc_ms()
            await _emit_lifecycle_event(
                session,
                media,
                "media.failed",
                reason_code="UNEXPECTED_ERROR",
            )
            await session.commit()
            logger.exception("Conversion error for media %d", media_id)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
