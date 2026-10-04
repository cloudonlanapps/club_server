"""Tests for /v1/media (#161). Parallel suite to test_uploads.py."""

import os
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_regular_admin_user,
    create_registered_user,
)


def _png_bytes() -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = (
        struct.pack(">I", 13)
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    )
    raw = b"\x00\xff\xff\xff"
    comp = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + comp)
    idat = (
        struct.pack(">I", len(comp))
        + b"IDAT"
        + comp
        + struct.pack(">I", idat_crc & 0xFFFFFFFF)
    )
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc & 0xFFFFFFFF)
    return signature + ihdr + idat + iend


@pytest.fixture(autouse=True)
def clean_upload_dir():
    upload_dir = os.environ.get("UPLOAD_DIR", "/tmp/club_server_test_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    yield
    shutil.rmtree(upload_dir, ignore_errors=True)
    tmp_base = tempfile.gettempdir()
    for d in os.listdir(tmp_base):
        if d.startswith("club_media_") or d.startswith("club_upload_"):
            shutil.rmtree(os.path.join(tmp_base, d), ignore_errors=True)


def _pdf_bytes() -> bytes:
    """The smallest thing the uploader accepts as a PDF."""
    return b"%PDF-1.7\n" + b"\x00" * 16


async def _upload_image(
    client: AsyncClient,
    token: str,
    *,
    preserve: bool = True,
    access_roles: str | None = None,
) -> dict:
    data: dict[str, str] = {"preserveOriginal": "true" if preserve else "false"}
    if access_roles is not None:
        data["accessRoles"] = access_roles
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "image/png")},
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R1")
@pytest.mark.requirement("media:R5")
async def test_upload_image_returns_201_with_uuid(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    assert "uuid" in body and body["uuid"]
    assert body["mediaType"] == "image"
    assert body["uploadedBy"] == "admin"
    assert body["accessRoles"] == ["public"]
    assert body["isEncrypted"] is False


@pytest.mark.asyncio
@pytest.mark.requirement("media:R3")
async def test_upload_rejects_unsupported_type(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    r = await client.post(
        "/v1/media",
        files={"file": ("x.txt", b"hi", "text/plain")},
        data={"preserveOriginal": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_MEDIA_TYPE"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R34")
@pytest.mark.requirement("media:R36")
async def test_list_media_admin_only(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_registered_user(db_session, "alice")
    await _upload_image(client, admin_token)

    r_member = await client.get(
        "/v1/media",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert r_member.status_code == 403

    r_admin = await client.get(
        "/v1/media",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r_admin.status_code == 200
    assert r_admin.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("media:R37")
async def test_myfiles_returns_only_caller_uploads(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin_token = await create_admin_user(db_session)
    alice_token = await create_registered_user(db_session, "alice")
    await _upload_image(client, admin_token)
    await _upload_image(client, alice_token)
    await _upload_image(client, alice_token)

    r = await client.get(
        "/v1/media/myfiles",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(it["uploadedBy"] == "alice" for it in body["items"])


@pytest.mark.asyncio
@pytest.mark.requirement("media:R38")
@pytest.mark.requirement("media:R39")
async def test_get_media_owner_sees_other_user_gets_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    alice_token = await create_registered_user(db_session, "alice")
    bob_token = await create_registered_user(db_session, "bob")
    body = await _upload_image(client, alice_token)
    mid = body["id"]

    r_alice = await client.get(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r_alice.status_code == 200

    r_bob = await client.get(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r_bob.status_code == 404
    assert r_bob.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R9")
@pytest.mark.requirement("media:R12")
@pytest.mark.requirement("media:R25")
async def test_download_public_image(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    r = await client.get(f"/v1/media/by_id/{body['uuid']}/download")
    assert r.status_code == 200
    assert r.content == _png_bytes()


@pytest.mark.asyncio
@pytest.mark.requirement("public:R32")
async def test_download_accepts_a_trailing_filename(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A decorative filename in the path, ignored for lookup (#424).

    It exists so the URL ends in a real extension — which is what a client
    reads to tell a picture from a video, since the uuid says nothing — and so
    a browser saves the file under a sensible name.
    """
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    uuid = body["uuid"]

    r = await client.get(f"/v1/media/by_id/{uuid}/download/{uuid[:8]}-photo.png")
    assert r.status_code == 200
    assert r.content == _png_bytes()

    # The name is not consulted, so a wrong one is not an error: the uuid is
    # the whole of the lookup and there is nothing to disagree with.
    r = await client.get(f"/v1/media/by_id/{uuid}/download/anything-at-all.txt")
    assert r.status_code == 200
    assert r.content == _png_bytes()

    # ...and an unknown uuid is still a miss, filename or not.
    r = await client.get("/v1/media/by_id/does-not-exist/download/x.png")
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("public:R32")
async def test_download_answers_head(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """HEAD gives the type without the body (#424).

    A client that only wants to know what a file is should not have to fetch a
    byte of a 44MB video to find out.
    """
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)

    r = await client.head(f"/v1/media/by_id/{body['uuid']}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b""


@pytest.mark.asyncio
@pytest.mark.requirement("media:R8")
@pytest.mark.requirement("media:R12")
async def test_should_serve_the_stored_type_when_converted_original_downloaded(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """#426: stored `mimeType` and served Content-Type agree after conversion.

    #331 asserted this and only ever exercised `preserveOriginal: true`, which
    is the one path where nothing converts. Under the default the file becomes
    WebP, and the column used to keep saying `image/png`.
    """
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token, preserve=False)

    assert body["mimeType"] == "image/webp"
    assert body["originalMimeType"] == "image/png"
    assert body["originalFilename"] == "img.png"

    r_dl = await client.get(
        f"/v1/media/by_id/{body['uuid']}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_dl.status_code == 200, r_dl.text
    assert r_dl.headers["content-type"].split(";")[0] == body["mimeType"]

    # The record names each fact once: what is stored, what was uploaded.
    record = await client.get(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert record.json()["mimeType"] == "image/webp"
    assert record.json()["originalMimeType"] == "image/png"
    assert record.json()["filename"] == body["filename"]


@pytest.mark.asyncio
@pytest.mark.requirement("public:R33")
@pytest.mark.requirement("media:R5")
async def test_should_refuse_a_poster_that_was_never_rendered(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """#426: a missing variant is a miss, not a different file.

    A PDF kept in its original form is never run through the converter, and
    the page image is a by-product of that conversion. Asking for the poster
    used to fall through to the PDF — a 4MB document answering a request for
    an image, with a 200 a client cannot recover from.
    """
    token = await create_admin_user(db_session)
    r = await client.post(
        "/v1/media",
        files={"file": ("form.pdf", _pdf_bytes(), "application/pdf")},
        data={"preserveOriginal": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    uuid = r.json()["uuid"]

    original = await client.get(
        f"/v1/media/by_id/{uuid}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert original.status_code == 200
    assert original.headers["content-type"].split(";")[0] == "application/pdf"

    poster = await client.get(
        f"/v1/media/by_id/{uuid}/download?variant=poster",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert poster.status_code == 404, poster.text
    assert poster.json()["detail"]["code"] == "FILE_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("public:R31")
@pytest.mark.requirement("media:R11")
async def test_descriptor_describes_the_converted_artifact(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """The published type and filename describe what is served (#424).

    A PNG uploaded without ``preserveOriginal`` is stored and served as WebP,
    while ``media.mime_type`` and ``original_filename`` still say PNG. A
    descriptor built from those would name the file wrongly, which is the very
    failure the descriptor exists to cure — so both come from the artifact.
    """
    token = await create_admin_user(db_session)
    uploaded = await _upload_image(client, token, preserve=False)
    uuid = uploaded["uuid"]

    r = await client.post(
        "/v1/users/by_id/admin/media",
        json={"tag": "avatar", "mediaUuid": uuid},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text

    rows = await client.get(
        "/v1/users/by_id/admin/media/avatar",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rows.status_code == 200, rows.text
    row = rows.json()[0]

    served = await client.get(
        f"/v1/media/by_id/{uuid}/download/{row['media']['filename']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].split(";")[0] == row["media"]["mimeType"]
    assert row["media"]["mimeType"] == "image/webp"
    assert row["media"]["filename"] == f"{uuid[:8]}-img.webp"

    # Each field is named for what it holds: the stored type, the uploaded
    # type, and the name a download is served under (#426).
    record = await client.get(
        f"/v1/media/by_id/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert record.status_code == 200, record.text
    media = record.json()
    assert media["originalFilename"] == "img.png"
    assert media["originalMimeType"] == "image/png"
    assert media["mimeType"] == "image/webp"
    assert media["filename"] == row["media"]["filename"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R13")
async def test_download_invalid_variant_for_image(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    r = await client.get(
        f"/v1/media/by_id/{body['uuid']}/download",
        params={"variant": "poster"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_VARIANT"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R41")
async def test_patch_access_roles(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    r = await client.patch(
        f"/v1/media/by_id/{body['id']}",
        json={"accessRoles": ["admin", "coach"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert set(r.json()["accessRoles"]) == {"admin", "coach"}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R41")
async def test_patch_unknown_field_ignored(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """MediaPatchRequest uses extra=ignore — unknown fields silently dropped."""
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    r = await client.patch(
        f"/v1/media/by_id/{body['id']}",
        json={"usageContext": "anything", "unknown": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("media:R21")
async def test_patch_invalid_access_role_value(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    r = await client.patch(
        f"/v1/media/by_id/{body['id']}",
        json={"accessRoles": ["not-a-role"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_ACCESS_ROLES"


@pytest.mark.asyncio
async def test_upload_stores_file_in_media_subdir(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """v2 media files live under upload_dir/media/, not the upload_dir root (#166)."""
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    uid = body["uuid"]

    root = Path(os.environ["UPLOAD_DIR"])
    assert (root / "media" / f"{uid}.png").exists()
    assert not (root / f"{uid}.png").exists()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R34")
@pytest.mark.requirement("media:R48")
async def test_soft_delete_preserves_file_on_disk(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Soft-delete must NOT touch on-disk artifacts (differs from v1)."""
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    uid = body["uuid"]

    upload_dir = Path(os.environ["UPLOAD_DIR"]) / "media"
    file_path = upload_dir / f"{uid}.png"
    assert file_path.exists()

    r = await client.delete(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204

    # File on disk preserved.
    assert file_path.exists()

    # Live list no longer includes the row.
    r_list = await client.get(
        "/v1/media",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_list.json()["total"] == 0

    # With includeDeleted it does.
    r_all = await client.get(
        "/v1/media",
        params={"includeDeleted": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_all.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("media:R49")
async def test_restore_clears_deleted_at(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    mid = body["id"]
    await client.delete(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    r = await client.post(
        f"/v1/media/by_id/{mid}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R52")
async def test_hard_delete_requires_soft_delete_first(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    mid = body["id"]
    r = await client.delete(
        f"/v1/media/by_id/{mid}/hard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "HARD_DELETE_NEEDS_SOFT_DELETE"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R51")
@pytest.mark.requirement("media:R53")
async def test_hard_delete_super_admin_only(
    client: AsyncClient,
    db_session: AsyncSession,
):
    super_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "thecoach")
    body = await _upload_image(client, super_token)
    mid = body["id"]
    uid = body["uuid"]

    # Coach cannot hard-delete even after soft-delete.
    await client.delete(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    r_coach = await client.delete(
        f"/v1/media/by_id/{mid}/hard",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r_coach.status_code == 403

    file_path = Path(os.environ["UPLOAD_DIR"]) / "media" / f"{uid}.png"
    assert file_path.exists()

    r_super = await client.delete(
        f"/v1/media/by_id/{mid}/hard",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r_super.status_code == 204
    assert not file_path.exists()

    r_get = await client.get(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r_get.status_code == 404


@pytest.mark.asyncio
async def test_links_placeholder_returns_empty_list(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(client, token)
    r = await client.get(
        f"/v1/media/by_id/{body['uuid']}/links",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
@pytest.mark.requirement("media:R38")
async def test_admin_can_see_others_media(
    client: AsyncClient,
    db_session: AsyncSession,
):
    alice_token = await create_registered_user(db_session, "alice")
    admin_token = await create_regular_admin_user(db_session, "boss")
    body = await _upload_image(client, alice_token)

    r = await client.get(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("media:R30")
async def test_private_image_blocks_anonymous_download(
    client: AsyncClient,
    db_session: AsyncSession,
):
    token = await create_admin_user(db_session)
    body = await _upload_image(
        client,
        token,
        access_roles='["admin","coach"]',
    )
    r = await client.get(f"/v1/media/by_id/{body['uuid']}/download")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R7")
async def test_should_store_real_type_when_client_sends_octet_stream(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """#331: a generic declared type must not be persisted verbatim."""
    token = await create_admin_user(db_session)
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "application/octet-stream")},
        data={"preserveOriginal": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["mimeType"] == "image/png"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R7")
async def test_should_store_real_type_when_client_declares_wrong_type(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """#331: the declared type is a hint; the bytes are authoritative."""
    token = await create_admin_user(db_session)
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "image/jpeg")},
        data={"preserveOriginal": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["mimeType"] == "image/png"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R9")
async def test_should_serve_the_stored_type_when_preserved_original_downloaded(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """#331: stored ``mimeType`` and served Content-Type must agree."""
    token = await create_admin_user(db_session)
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "application/octet-stream")},
        data={"preserveOriginal": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    r_dl = await client.get(
        f"/v1/media/by_id/{body['uuid']}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_dl.status_code == 200, r_dl.text
    assert r_dl.headers["content-type"].split(";")[0] == body["mimeType"]
