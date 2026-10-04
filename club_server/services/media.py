"""v2 media service (#161). Parallel to ``services.upload``.

Reuses the conversion + encryption helpers from ``services.upload`` so the
two systems stay in sync on supported formats and crypto behavior.
"""

import json
import os
import re
import shutil
import tempfile
import uuid as uuid_mod
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.media import Media
from ..db.models.user import Role, User
from ..exceptions import (
    DecryptionFailedException,
    EncryptedFileTooLargeException,
    EncryptionNotConfiguredException,
    EncryptionNotSupportedForVideoException,
    FileTooLargeException,
    HardDeleteNeedsSoftDeleteException,
    MediaFileMissingException,
    MediaNotFoundException,
    NothingToRestoreException,
)
from ..schemas.common import PaginatedResponse
from ..schemas.media import MediaResponse
from ..utils import now_utc_ms
from . import encryption as enc
from .media_mime import FALLBACK_MIME_TYPE, MIME_BY_EXTENSION, resolve_mime_type
from .media_pipeline import (
    classify_media,
    normalize_access_roles,
    run_image_conversion,
    pdf_poster_path,
    run_pdf_conversion,
)


def resolve_media_file_path(media: Media, variant: str = "original") -> Path:
    """Derive the on-disk path for a media row + variant.

    Variants:
    - image: ``original`` (only)
    - video: ``original``, ``poster``, ``animated``
    - pdf:   ``original``, ``poster``

    Encrypted artifacts get a ``.enc`` suffix on disk (#150).
    """
    upload_dir = Path(settings.upload_dir) / "media"
    uid = media.uuid
    suffix = ".enc" if media.is_encrypted else ""

    if media.media_type == "image":
        if media.preserve_original:
            return upload_dir / f"{uid}.{media.original_extension}{suffix}"
        return upload_dir / f"{uid}.webp{suffix}"

    if media.media_type == "pdf":
        # The poster resolves to the poster path whether or not one was
        # rendered. Falling through to the PDF, as this once did, answered a
        # request for a page image with a 4MB document and a 200 — a client
        # cannot recover from that, where it can from the 404 the caller's
        # existence check now produces (#426).
        if variant == "poster":
            return upload_dir / f"{uid}_poster.png{suffix}"
        return upload_dir / f"{uid}.pdf{suffix}"

    # video
    if variant == "animated":
        return upload_dir / f"{uid}_animated.webp"
    if variant == "poster":
        return upload_dir / f"{uid}_poster.webp"
    # default: original video
    if media.preserve_original:
        return upload_dir / f"{uid}.{media.original_extension}"
    return upload_dir / f"{uid}.mp4"


def served_extension(media: Media) -> str:
    """The extension of what ``?variant=original`` actually returns (#424).

    Not the uploaded one. An image uploaded as PNG without ``preserveOriginal``
    is stored and served as WebP, and ``media.original_filename`` still says
    ``.png``; a URL ending in the uploaded extension would therefore describe
    the file wrongly, which is the exact failure this is meant to cure.
    """
    name = resolve_media_file_path(media, "original").name
    if name.endswith(".enc"):
        name = name[: -len(".enc")]
    return Path(name).suffix.lower()


def served_mime_type(media: Media) -> str:
    """The ``Content-Type`` a download of the original will carry.

    Write-time only: this is what ``create_media`` stores in ``mime_type``
    (#426). A reader wants the column, which is authoritative and needs no
    derivation. Falls back to the uploaded type for an extension the mime
    table does not know.
    """
    return MIME_BY_EXTENSION.get(served_extension(media), media.original_mime_type)


def download_filename(media: Media) -> str:
    """A safe, unique-enough filename for the download URL (#424).

    Two events can easily attach files with the same basename. The URL itself
    cannot collide — the uuid is a path segment — but two identically named
    files landing in someone's Downloads folder is a real outcome, so the name
    carries the first eight characters of the uuid.

    The stem comes from the upload and the extension from what is served, so
    the URL ends in an extension a renderer can trust — a PNG stored as WebP
    is named ``.webp`` here, matching ``mime_type`` (#426).
    """
    name = media.original_filename or "file"
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "file"
    return f"{media.uuid[:8]}-{stem}{served_extension(media)}"


def _role_names(user: User) -> set[str]:
    """The role names stored on ``user``.

    Avoids importing dependencies.get_user_roles (which would create a
    routing-layer dependency from the service layer) by parsing the same
    JSON column directly.
    """
    try:
        return set(json.loads(user.roles).get("roles", [])) if user.roles else set()
    except (ValueError, TypeError, AttributeError):
        return set()


def can_view_media(media: Media, viewer: User | None) -> bool:
    """Return True iff ``viewer`` may see this media row's content/metadata.

    Mirrors the per-file gate used by ``GET /v1/media/by_id/{uuid}/download``
    so link-row listings cannot leak the existence of media the caller is
    not allowed to download (#167).
    """
    try:
        roles = json.loads(media.access_roles) if media.access_roles else ["public"]
    except (ValueError, TypeError):
        roles = ["public"]

    if "public" in roles:
        return True
    if viewer is None:
        return False
    if viewer.is_super_admin:
        return True

    viewer_role_set = _role_names(viewer)

    if "self" in roles and viewer.username == media.uploaded_by:
        return True
    if "admin" in roles and Role.admin.value in viewer_role_set:
        return True
    if "coach" in roles and Role.coach.value in viewer_role_set:
        return True
    return False


def can_modify_media(media: Media, user: User) -> bool:
    """Return True iff ``user`` may mutate this media row (#503).

    Used by the access-roles change, soft-delete and restore endpoints.
    Only the uploader, an admin and the super admin may, whatever the
    item's access roles; being able to view it is not enough.
    """
    if user.is_super_admin:
        return True
    if user.username == media.uploaded_by:
        return True
    return Role.admin.value in _role_names(user)


def allowed_variants(media_type: str) -> list[str]:
    if media_type == "image":
        return ["original"]
    if media_type == "pdf":
        return ["original", "poster"]
    return ["original", "poster", "animated"]


@dataclass
class DownloadResult:
    """Resolved download payload from ``MediaService.resolve_download``.

    Exactly one of ``content`` (decrypted bytes, served inline) or
    ``file_path`` (plain artifact, streamed from disk) is set; ``media_type``
    is the response MIME type for either.
    """

    media_type: str
    content: bytes | None = None
    file_path: Path | None = None


class MediaService:
    """Service for v2 media management."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def create_media(
        self,
        file: UploadFile,
        uploaded_by: str,
        preserve_original: bool,
        duration: float | None = None,
        start: float | None = None,
        access_roles: list[str] | None = None,
        encrypt: bool = False,
    ) -> Media:
        """Accept an uploaded file, optionally convert, persist to DB."""
        filename = file.filename or "unnamed"
        content_type = file.content_type
        media_type, extension = classify_media(filename, content_type)
        normalized_roles = normalize_access_roles(access_roles)
        access_roles_json = json.dumps(normalized_roles)

        if encrypt:
            if media_type == "video":
                raise EncryptionNotSupportedForVideoException()
            if not enc.is_configured():
                raise EncryptionNotConfiguredException()

        content = await file.read()
        mime_type = resolve_mime_type(filename, content_type, content, media_type)
        if media_type == "image":
            max_mb = settings.max_image_upload_size_mb
        elif media_type == "pdf":
            max_mb = settings.max_pdf_upload_size_mb
        else:
            max_mb = settings.max_video_upload_size_mb
        if len(content) > max_mb * 1024 * 1024:
            raise FileTooLargeException(max_mb, media_type=media_type)
        if (
            encrypt
            and len(content) > settings.max_encrypted_upload_size_mb * 1024 * 1024
        ):
            raise EncryptedFileTooLargeException(
                settings.max_encrypted_upload_size_mb,
            )

        uid = str(uuid_mod.uuid4())
        upload_dir = Path(settings.upload_dir) / "media"
        upload_dir.mkdir(parents=True, exist_ok=True)

        now = now_utc_ms()
        conversion_params_json = None
        if duration is not None or start is not None:
            conversion_params_json = json.dumps({"duration": duration, "start": start})

        tmp_dir = Path(tempfile.mkdtemp(prefix="club_media_"))
        input_path = tmp_dir / f"{uid}_input.{extension}"
        input_path.write_bytes(content)

        try:
            if media_type == "image":
                return await self._handle_image(
                    uid=uid,
                    filename=filename,
                    extension=extension,
                    mime_type=mime_type,
                    preserve_original=preserve_original,
                    uploaded_by=uploaded_by,
                    input_path=input_path,
                    tmp_dir=tmp_dir,
                    upload_dir=upload_dir,
                    now=now,
                    access_roles_json=access_roles_json,
                    encrypt=encrypt,
                )
            if media_type == "pdf":
                return await self._handle_pdf(
                    uid=uid,
                    filename=filename,
                    extension=extension,
                    mime_type=mime_type,
                    preserve_original=preserve_original,
                    uploaded_by=uploaded_by,
                    input_path=input_path,
                    tmp_dir=tmp_dir,
                    upload_dir=upload_dir,
                    now=now,
                    access_roles_json=access_roles_json,
                    encrypt=encrypt,
                )
            return await self._handle_video(
                uid=uid,
                filename=filename,
                extension=extension,
                mime_type=mime_type,
                preserve_original=preserve_original,
                uploaded_by=uploaded_by,
                input_path=input_path,
                tmp_dir=tmp_dir,
                upload_dir=upload_dir,
                now=now,
                conversion_params_json=conversion_params_json,
                access_roles_json=access_roles_json,
            )
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    async def _handle_image(
        self,
        uid,
        filename,
        extension,
        mime_type,
        preserve_original,
        uploaded_by,
        input_path,
        tmp_dir,
        upload_dir,
        now,
        access_roles_json,
        encrypt,
    ) -> Media:
        if preserve_original:
            staged = tmp_dir / f"{uid}.{extension}"
            shutil.move(str(input_path), str(staged))
            conversion_status = "none"
            dest_name = f"{uid}.{extension}"
        else:
            output_path = tmp_dir / f"{uid}.webp"
            await run_image_conversion(input_path, output_path)
            staged = tmp_dir / f"{uid}.webp"
            conversion_status = "completed"
            dest_name = f"{uid}.webp"

        encryption_meta: str | None = None
        is_encrypted = 0
        encryption_version: int | None = None
        if encrypt:
            plaintext = staged.read_bytes()
            ciphertext, meta_json = enc.encrypt_payload(plaintext)
            dest = upload_dir / f"{dest_name}.enc"
            dest.write_bytes(ciphertext)
            encryption_meta = meta_json
            is_encrypted = 1
            encryption_version = enc.ENCRYPTION_VERSION
            file_size = dest.stat().st_size
        else:
            dest = upload_dir / dest_name
            shutil.move(str(staged), str(dest))
            file_size = dest.stat().st_size

        shutil.rmtree(tmp_dir, ignore_errors=True)

        media = Media(
            uuid=uid,
            original_filename=filename,
            media_type="image",
            mime_type=mime_type,
            original_mime_type=mime_type,
            original_extension=extension,
            file_size=file_size,
            preserve_original=1 if preserve_original else 0,
            conversion_status=conversion_status,
            uploaded_by=uploaded_by,
            access_roles=access_roles_json,
            is_encrypted=is_encrypted,
            encryption_version=encryption_version,
            encryption_meta=encryption_meta,
            created_at=now,
            updated_at=now,
        )
        # The row is built with the uploaded type in both columns, then
        # `mime_type` is corrected to the artifact actually written: a PNG
        # stored without `preserve_original` is served as WebP (#426). Done
        # here rather than inline so `resolve_media_file_path` stays the one
        # place that knows which file a variant resolves to.
        media.mime_type = served_mime_type(media)
        self.db.add(media)
        await self.db.flush()
        return media

    async def _handle_pdf(
        self,
        uid,
        filename,
        extension,
        mime_type,
        preserve_original,
        uploaded_by,
        input_path,
        tmp_dir,
        upload_dir,
        now,
        access_roles_json,
        encrypt,
    ) -> Media:
        if preserve_original:
            staged_pdf = tmp_dir / f"{uid}.pdf"
            shutil.move(str(input_path), str(staged_pdf))
            poster_src: Path | None = None
            conversion_status = "none"
        else:
            output_path = tmp_dir / f"{uid}.pdf"
            await run_pdf_conversion(input_path, output_path)
            staged_pdf = output_path
            poster_src = pdf_poster_path(output_path)
            conversion_status = "completed"

        encryption_meta: str | None = None
        is_encrypted = 0
        encryption_version: int | None = None

        if encrypt:
            pdf_plain = staged_pdf.read_bytes()
            ciphertext, meta_json = enc.encrypt_payload(pdf_plain)
            dest_pdf = upload_dir / f"{uid}.pdf.enc"
            dest_pdf.write_bytes(ciphertext)
            if poster_src is not None:
                poster_cipher = enc.encrypt_poster(poster_src.read_bytes(), meta_json)
                (upload_dir / f"{uid}_poster.png.enc").write_bytes(poster_cipher)
            encryption_meta = meta_json
            is_encrypted = 1
            encryption_version = enc.ENCRYPTION_VERSION
            file_size = dest_pdf.stat().st_size
        else:
            dest_pdf = upload_dir / f"{uid}.pdf"
            shutil.move(str(staged_pdf), str(dest_pdf))
            file_size = dest_pdf.stat().st_size
            if poster_src is not None:
                shutil.move(str(poster_src), str(upload_dir / f"{uid}_poster.png"))

        shutil.rmtree(tmp_dir, ignore_errors=True)

        media = Media(
            uuid=uid,
            original_filename=filename,
            media_type="pdf",
            mime_type=mime_type,
            original_mime_type=mime_type,
            original_extension=extension,
            file_size=file_size,
            preserve_original=1 if preserve_original else 0,
            conversion_status=conversion_status,
            uploaded_by=uploaded_by,
            access_roles=access_roles_json,
            is_encrypted=is_encrypted,
            encryption_version=encryption_version,
            encryption_meta=encryption_meta,
            created_at=now,
            updated_at=now,
        )
        # The row is built with the uploaded type in both columns, then
        # `mime_type` is corrected to the artifact actually written: a PNG
        # stored without `preserve_original` is served as WebP (#426). Done
        # here rather than inline so `resolve_media_file_path` stays the one
        # place that knows which file a variant resolves to.
        media.mime_type = served_mime_type(media)
        self.db.add(media)
        await self.db.flush()
        return media

    async def _handle_video(
        self,
        uid,
        filename,
        extension,
        mime_type,
        preserve_original,
        uploaded_by,
        input_path,
        tmp_dir,
        upload_dir,
        now,
        conversion_params_json,
        access_roles_json,
    ) -> Media:
        # Stage the uploaded bytes at a stable, worker-discoverable path so the
        # background video worker can find them after the request commits.
        pending_dir = upload_dir / "_pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        staged_input = pending_dir / f"{uid}_input.{extension}"
        shutil.move(str(input_path), str(staged_input))
        shutil.rmtree(tmp_dir, ignore_errors=True)

        media = Media(
            uuid=uid,
            original_filename=filename,
            media_type="video",
            mime_type=mime_type,
            original_mime_type=mime_type,
            original_extension=extension,
            file_size=os.path.getsize(staged_input),
            preserve_original=1 if preserve_original else 0,
            conversion_status="pending",
            conversion_params=conversion_params_json,
            uploaded_by=uploaded_by,
            access_roles=access_roles_json,
            created_at=now,
            updated_at=now,
        )
        # The row is built with the uploaded type in both columns, then
        # `mime_type` is corrected to the artifact actually written: a PNG
        # stored without `preserve_original` is served as WebP (#426). Done
        # here rather than inline so `resolve_media_file_path` stays the one
        # place that knows which file a variant resolves to.
        media.mime_type = served_mime_type(media)
        self.db.add(media)
        await self.db.flush()
        return media

    async def get(self, media_id: int, include_deleted: bool = False) -> Media:
        q = select(Media).where(Media.id == media_id)
        if not include_deleted:
            q = q.where(Media.deleted_at.is_(None))
        media = (await self.db.execute(q)).scalar_one_or_none()
        if not media:
            raise MediaNotFoundException(media_id)
        return media

    async def get_by_uuid(self, uuid: str, include_deleted: bool = False) -> Media:
        q = select(Media).where(Media.uuid == uuid)
        if not include_deleted:
            q = q.where(Media.deleted_at.is_(None))
        media = (await self.db.execute(q)).scalar_one_or_none()
        if not media:
            raise MediaNotFoundException(uuid)
        return media

    def resolve_download(self, media: Media, file_path: Path) -> DownloadResult:
        """Select the response bytes/path and MIME type for a download.

        The caller is responsible for conversion-status, variant, access, and
        file-existence checks; this method only handles the encrypted-vs-plain
        branching and MIME-type selection. Encrypted artifacts are decrypted in
        memory and returned as ``content``; plain artifacts are returned as a
        ``file_path`` to stream from disk.

        Raises ``DecryptionFailedException`` (missing metadata or AES-GCM auth
        failure) or ``EncryptionNotConfiguredException`` when the encrypted
        branch cannot produce plaintext.
        """
        if media.is_encrypted:
            inner_suffix = file_path.with_suffix("").suffix.lower()
            media_type = MIME_BY_EXTENSION.get(inner_suffix, media.mime_type)
            if not media.encryption_meta:
                raise DecryptionFailedException("Encryption metadata missing")
            ciphertext = file_path.read_bytes()
            is_poster = file_path.name.endswith("_poster.png.enc")
            plaintext = enc.decrypt(ciphertext, media.encryption_meta, poster=is_poster)
            return DownloadResult(media_type=media_type, content=plaintext)

        media_type = MIME_BY_EXTENSION.get(file_path.suffix.lower(), FALLBACK_MIME_TYPE)
        return DownloadResult(media_type=media_type, file_path=file_path)

    async def list_media(
        self,
        offset: int = 0,
        limit: int = 20,
        media_type: str | None = None,
        conversion_status: str | None = None,
        include_deleted: bool = False,
    ) -> PaginatedResponse[MediaResponse]:
        base = select(Media)
        count = select(func.count()).select_from(Media)
        if not include_deleted:
            base = base.where(Media.deleted_at.is_(None))
            count = count.where(Media.deleted_at.is_(None))
        if media_type:
            base = base.where(Media.media_type == media_type)
            count = count.where(Media.media_type == media_type)
        if conversion_status:
            base = base.where(Media.conversion_status == conversion_status)
            count = count.where(Media.conversion_status == conversion_status)

        total = (await self.db.execute(count)).scalar_one()
        rows = (
            (
                await self.db.execute(
                    base.offset(offset).limit(limit).order_by(Media.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return PaginatedResponse(
            items=[MediaResponse.from_model(m) for m in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_user_media(
        self,
        uploaded_by: str,
        offset: int = 0,
        limit: int = 20,
        media_type: str | None = None,
        conversion_status: str | None = None,
    ) -> PaginatedResponse[MediaResponse]:
        filt = (Media.deleted_at.is_(None), Media.uploaded_by == uploaded_by)
        base = select(Media).where(*filt)
        count = select(func.count()).select_from(Media).where(*filt)
        if media_type:
            base = base.where(Media.media_type == media_type)
            count = count.where(Media.media_type == media_type)
        if conversion_status:
            base = base.where(Media.conversion_status == conversion_status)
            count = count.where(Media.conversion_status == conversion_status)
        total = (await self.db.execute(count)).scalar_one()
        rows = (
            (
                await self.db.execute(
                    base.offset(offset).limit(limit).order_by(Media.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return PaginatedResponse(
            items=[MediaResponse.from_model(m) for m in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def update_access_roles(
        self,
        media_id: int,
        access_roles: list[str],
    ) -> tuple[Media, list[str]]:
        normalized = normalize_access_roles(access_roles)
        media = await self.get(media_id)
        old = json.loads(media.access_roles) if media.access_roles else []
        media.access_roles = json.dumps(normalized)
        media.updated_at = now_utc_ms()
        await self.db.flush()
        return media, old

    async def encrypt_in_place(self, uuid: str) -> tuple[Media, bool]:
        """Encrypt an already-stored plaintext artifact in place (#285).

        Single-direction and idempotent: returns ``(media, False)`` when the
        row is already encrypted (no-op), ``(media, True)`` when this call
        performed the encryption. Used by the admin backfill sweep to encrypt
        identity documents (and any other media) uploaded before encryption was
        switched on; it reuses the same ``encryption`` primitives as the upload
        path so on-disk layout stays identical.

        Crash-safe ordering: the ``.enc`` artifact(s) are written, then the row
        is flipped and flushed, and only then is the plaintext original
        unlinked. Any failure before the unlink rolls the row back with the
        plaintext intact, so a re-run re-encrypts cleanly (the stale ``.enc`` is
        overwritten). Mirrors ``hard_delete``'s file-then-flush pattern.
        """
        media = await self.get_by_uuid(uuid)
        if media.is_encrypted:
            return media, False
        if media.media_type == "video":
            raise EncryptionNotSupportedForVideoException()
        if not enc.is_configured():
            raise EncryptionNotConfiguredException()

        upload_dir = Path(settings.upload_dir) / "media"
        uid = media.uuid

        # ``is_encrypted`` is still 0 here, so this resolves to the plaintext path.
        main_path = resolve_media_file_path(media, "original")
        if not main_path.exists():
            raise MediaFileMissingException(uuid)

        ciphertext, meta_json = enc.encrypt_payload(main_path.read_bytes())
        enc_main = main_path.with_name(main_path.name + ".enc")
        enc_main.write_bytes(ciphertext)

        # PDFs carry a converted poster alongside the document; encrypt it under
        # the same DEK so the poster variant keeps downloading after the flip.
        plain_poster: Path | None = None
        if media.media_type == "pdf" and not media.preserve_original:
            candidate = upload_dir / f"{uid}_poster.png"
            if candidate.exists():
                plain_poster = candidate
                poster_cipher = enc.encrypt_poster(candidate.read_bytes(), meta_json)
                (upload_dir / f"{uid}_poster.png.enc").write_bytes(poster_cipher)

        # Flip the row (and flush) before removing any plaintext.
        media.is_encrypted = 1
        media.encryption_version = enc.ENCRYPTION_VERSION
        media.encryption_meta = meta_json
        media.file_size = enc_main.stat().st_size
        media.updated_at = now_utc_ms()
        await self.db.flush()

        # Plaintext originals come off disk last.
        main_path.unlink(missing_ok=True)
        if plain_poster is not None:
            plain_poster.unlink(missing_ok=True)

        return media, True

    async def soft_delete(self, media_id: int) -> Media:
        """Mark deleted_at. Files on disk are preserved (#161 differs from v1)."""
        media = await self.get(media_id)
        now = now_utc_ms()
        media.deleted_at = now
        media.updated_at = now
        await self.db.flush()
        return media

    async def restore(self, media_id: int) -> Media:
        """Clear deleted_at. A live item is refused and left untouched (#520)."""
        media = await self.get(media_id, include_deleted=True)
        if media.deleted_at is None:
            raise NothingToRestoreException("Media", media_id)
        media.deleted_at = None
        media.updated_at = now_utc_ms()
        await self.db.flush()
        return media

    async def hard_delete(self, media_id: int) -> Media:
        """Permanent delete. Requires the row to already be soft-deleted.

        Removes the DB row and any on-disk artifacts.
        """
        media = await self.get(media_id, include_deleted=True)
        if media.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("Media", media_id)

        upload_dir = Path(settings.upload_dir) / "media"
        uid = media.uuid
        patterns = [
            f"{uid}.webp",
            f"{uid}.mp4",
            f"{uid}.pdf",
            f"{uid}.{media.original_extension}",
            f"{uid}_poster.webp",
            f"{uid}_poster.png",
            f"{uid}_animated.webp",
            f"{uid}.webp.enc",
            f"{uid}.pdf.enc",
            f"{uid}.{media.original_extension}.enc",
            f"{uid}_poster.png.enc",
        ]
        for p in patterns:
            fp = upload_dir / p
            if fp.exists():
                fp.unlink()

        tmp_base = Path(tempfile.gettempdir())
        for d in tmp_base.iterdir():
            if d.is_dir() and d.name.startswith("club_media_"):
                try:
                    if any(f.name.startswith(uid) for f in d.iterdir()):
                        shutil.rmtree(d, ignore_errors=True)
                except FileNotFoundError:
                    pass

        await self.db.delete(media)
        await self.db.flush()
        return media
