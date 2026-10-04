"""Issue 285: encrypt existing plaintext media in place.

Covers the ``MediaService.encrypt_in_place`` backfill primitive (image + PDF
round-trips, idempotency, guards) and the super-admin
``POST /v1/media/by_id/{uuid}/encrypt`` endpoint plus the
``isEncrypted`` filter on ``GET /v1/media/links`` that the sweep uses to
enumerate plaintext identity documents.

A real test KEK is configured via ``monkeypatch`` on ``settings.encryption_key``
(the encryption helpers read it live through ``_load_kek``), and ``upload_dir``
is redirected to an isolated tmp dir so on-disk artifacts don't collide.
"""

import base64
import uuid as _uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.media import Media
from club_server.exceptions import (
    EncryptionNotConfiguredException,
    EncryptionNotSupportedForVideoException,
    MediaFileMissingException,
)
from club_server.services import encryption as enc
from club_server.services.media import MediaService, resolve_media_file_path
from club_server.utils import now_utc_ms

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_member_user,
    create_regular_admin_user,
)

# A valid base64-encoded 32-byte KEK for the encryption helpers.
_TEST_KEK = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    """Redirect ``settings.upload_dir`` to an isolated tmp dir; yield media/."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    d = tmp_path / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def kek(monkeypatch):
    """Configure a real test KEK for the duration of the test."""
    monkeypatch.setattr(settings, "encryption_key", _TEST_KEK)


async def _seed_image(
    db_session: AsyncSession,
    media_dir,
    *,
    content: bytes = b"original-image-bytes",
    preserve_original: bool = False,
    ext: str = "jpg",
) -> Media:
    uid = str(_uuid.uuid4())
    name = f"{uid}.{ext}" if preserve_original else f"{uid}.webp"
    (media_dir / name).write_bytes(content)
    now = now_utc_ms()
    media = Media(
        uuid=uid,
        original_filename="photo.jpg",
        media_type="image",
        mime_type="image/jpeg",
        original_mime_type="image/jpeg",
        original_extension=ext,
        file_size=len(content),
        preserve_original=1 if preserve_original else 0,
        conversion_status="completed",
        uploaded_by=None,
        access_roles='["self", "admin"]',
        is_encrypted=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(media)
    await db_session.flush()
    return media


async def _seed_pdf(
    db_session: AsyncSession,
    media_dir,
    *,
    pdf: bytes = b"pdf-document-bytes",
    poster: bytes | None = b"poster-png-bytes",
) -> Media:
    uid = str(_uuid.uuid4())
    (media_dir / f"{uid}.pdf").write_bytes(pdf)
    if poster is not None:
        (media_dir / f"{uid}_poster.png").write_bytes(poster)
    now = now_utc_ms()
    media = Media(
        uuid=uid,
        original_filename="doc.pdf",
        media_type="pdf",
        mime_type="application/pdf",
        original_mime_type="application/pdf",
        original_extension="pdf",
        file_size=len(pdf),
        preserve_original=0,
        conversion_status="completed",
        uploaded_by=None,
        access_roles='["self", "admin"]',
        is_encrypted=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(media)
    await db_session.flush()
    return media


# --------------------------------------------------------------------------
# Service-level: encrypt_in_place
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R88")
async def test_issue_285_encrypts_image_in_place_and_round_trips(
    db_session, media_dir, kek
):
    media = await _seed_image(db_session, media_dir, content=b"the-original-bytes")
    plain_path = resolve_media_file_path(media, "original")
    assert plain_path.exists()

    svc = MediaService(db_session)
    result, did_encrypt = await svc.encrypt_in_place(media.uuid)

    assert did_encrypt is True
    assert bool(result.is_encrypted) is True
    assert result.encryption_version == enc.ENCRYPTION_VERSION
    assert result.encryption_meta

    # Plaintext original removed; the .enc artifact is present.
    assert not plain_path.exists()
    enc_path = resolve_media_file_path(result, "original")
    assert enc_path.name.endswith(".webp.enc")
    assert enc_path.exists()
    assert result.file_size == enc_path.stat().st_size

    # The decrypt path round-trips back to the original bytes.
    download = svc.resolve_download(result, enc_path)
    assert download.content == b"the-original-bytes"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R88")
async def test_issue_285_encrypts_preserve_original_image(db_session, media_dir, kek):
    media = await _seed_image(
        db_session, media_dir, content=b"raw", preserve_original=True, ext="png"
    )
    plain_path = resolve_media_file_path(media, "original")
    assert plain_path.name.endswith(".png")

    result, did_encrypt = await MediaService(db_session).encrypt_in_place(media.uuid)

    assert did_encrypt is True
    assert not plain_path.exists()
    assert resolve_media_file_path(result, "original").name.endswith(".png.enc")


@pytest.mark.asyncio
@pytest.mark.requirement("media:R88")
async def test_issue_285_encrypts_pdf_with_poster_round_trips(
    db_session, media_dir, kek
):
    media = await _seed_pdf(
        db_session, media_dir, pdf=b"PDF-BODY", poster=b"POSTER-BODY"
    )
    uid = media.uuid

    svc = MediaService(db_session)
    result, did_encrypt = await svc.encrypt_in_place(uid)

    assert did_encrypt is True
    # Both encrypted artifacts written, both plaintext originals removed.
    assert (media_dir / f"{uid}.pdf.enc").exists()
    assert (media_dir / f"{uid}_poster.png.enc").exists()
    assert not (media_dir / f"{uid}.pdf").exists()
    assert not (media_dir / f"{uid}_poster.png").exists()

    # Document and poster both decrypt back to their originals (same DEK).
    pdf_dl = svc.resolve_download(result, media_dir / f"{uid}.pdf.enc")
    assert pdf_dl.content == b"PDF-BODY"
    poster_dl = svc.resolve_download(result, media_dir / f"{uid}_poster.png.enc")
    assert poster_dl.content == b"POSTER-BODY"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R88")
async def test_issue_285_encrypts_pdf_without_poster(db_session, media_dir, kek):
    media = await _seed_pdf(db_session, media_dir, poster=None)
    uid = media.uuid

    result, did_encrypt = await MediaService(db_session).encrypt_in_place(uid)

    assert did_encrypt is True
    assert (media_dir / f"{uid}.pdf.enc").exists()
    assert not (media_dir / f"{uid}_poster.png.enc").exists()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R89")
async def test_issue_285_encrypt_is_idempotent(db_session, media_dir, kek):
    media = await _seed_image(db_session, media_dir)
    svc = MediaService(db_session)

    _, did_first = await svc.encrypt_in_place(media.uuid)
    enc_path = resolve_media_file_path(media, "original")
    cipher_after_first = enc_path.read_bytes()

    result, did_second = await svc.encrypt_in_place(media.uuid)

    assert did_first is True
    assert did_second is False  # no-op on an already-encrypted row
    assert bool(result.is_encrypted) is True
    # The ciphertext was not rewritten (no fresh DEK / re-encryption).
    assert enc_path.read_bytes() == cipher_after_first


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_video_rejected(db_session, media_dir, kek):
    uid = str(_uuid.uuid4())
    now = now_utc_ms()
    media = Media(
        uuid=uid,
        original_filename="clip.mp4",
        media_type="video",
        mime_type="video/mp4",
        original_mime_type="video/mp4",
        original_extension="mp4",
        file_size=10,
        preserve_original=0,
        conversion_status="completed",
        uploaded_by=None,
        access_roles='["self", "admin"]',
        is_encrypted=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(media)
    await db_session.flush()

    with pytest.raises(EncryptionNotSupportedForVideoException):
        await MediaService(db_session).encrypt_in_place(uid)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_requires_configured_kek(
    db_session, media_dir, monkeypatch
):
    monkeypatch.setattr(settings, "encryption_key", None)
    media = await _seed_image(db_session, media_dir)

    with pytest.raises(EncryptionNotConfiguredException):
        await MediaService(db_session).encrypt_in_place(media.uuid)

    # The row is untouched and the plaintext is still on disk.
    assert bool(media.is_encrypted) is False
    assert resolve_media_file_path(media, "original").exists()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_missing_file_raises(db_session, media_dir, kek):
    media = await _seed_image(db_session, media_dir)
    resolve_media_file_path(media, "original").unlink()

    with pytest.raises(MediaFileMissingException):
        await MediaService(db_session).encrypt_in_place(media.uuid)

    assert bool(media.is_encrypted) is False


# --------------------------------------------------------------------------
# HTTP: POST /v1/media/by_id/{uuid}/encrypt
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R88")
async def test_issue_285_encrypt_endpoint_super_admin_encrypts(
    client: AsyncClient, db_session, media_dir, kek
):
    token = await create_admin_user(db_session)
    media = await _seed_image(db_session, media_dir, content=b"abc")

    r = await client.post(
        f"/v1/media/by_id/{media.uuid}/encrypt",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["isEncrypted"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("media:R89")
async def test_issue_285_encrypt_endpoint_idempotent(
    client: AsyncClient, db_session, media_dir, kek
):
    token = await create_admin_user(db_session)
    media = await _seed_image(db_session, media_dir)
    uuid = media.uuid  # capture before requests expire the ORM object
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post(f"/v1/media/by_id/{uuid}/encrypt", headers=headers)
    second = await client.post(f"/v1/media/by_id/{uuid}/encrypt", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["isEncrypted"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_endpoint_regular_admin_forbidden(
    client: AsyncClient, db_session, media_dir, kek
):
    admin_token = await create_regular_admin_user(db_session, "boss")
    media = await _seed_image(db_session, media_dir)
    uuid = media.uuid
    plain_path = resolve_media_file_path(media, "original")

    r = await client.post(
        f"/v1/media/by_id/{uuid}/encrypt",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert r.status_code == 403, r.text
    # The row stays plaintext.
    assert plain_path.exists()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_endpoint_unauthenticated(
    client: AsyncClient, db_session, media_dir, kek
):
    media = await _seed_image(db_session, media_dir)
    r = await client.post(f"/v1/media/by_id/{media.uuid}/encrypt")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_endpoint_unknown_uuid_404(
    client: AsyncClient, db_session, media_dir, kek
):
    token = await create_admin_user(db_session)
    r = await client.post(
        f"/v1/media/by_id/{_uuid.uuid4()}/encrypt",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("media:R90")
async def test_issue_285_encrypt_endpoint_not_configured_503(
    client: AsyncClient, db_session, media_dir, monkeypatch
):
    monkeypatch.setattr(settings, "encryption_key", None)
    token = await create_admin_user(db_session)
    media = await _seed_image(db_session, media_dir)

    r = await client.post(
        f"/v1/media/by_id/{media.uuid}/encrypt",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "ENCRYPTION_NOT_CONFIGURED"


# --------------------------------------------------------------------------
# HTTP: GET /v1/media/links?isEncrypted=false (sweep enumeration)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R80")
async def test_issue_285_links_filter_by_encryption_state(
    client: AsyncClient, db_session, media_dir
):
    token = await create_admin_user(db_session)
    await create_member_user(db_session, "skater")

    plaintext_uuid = await attach_identity_document(db_session, "skater")
    encrypted_uuid = await attach_identity_document(db_session, "skater")
    # Flip one of the two identity docs to encrypted.
    enc_media = await MediaService(db_session).get_by_uuid(encrypted_uuid)
    enc_media.is_encrypted = 1
    await db_session.flush()

    r = await client.get(
        "/v1/media/links",
        params={"tag": "identity_document", "isEncrypted": "false"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    returned = {item["mediaUuid"] for item in body["items"]}
    assert plaintext_uuid in returned
    assert encrypted_uuid not in returned
    assert all(item["isEncrypted"] is False for item in body["items"])
