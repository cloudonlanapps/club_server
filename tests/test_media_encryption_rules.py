"""Encryption-at-rest and download cache-header rules of media_requirements.md (#494)."""

import base64
import os
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings

from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    mp4_bytes,
    pdf_bytes,
    png_bytes,
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

_TEST_KEK = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture
def kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a real encryption key for one test."""
    monkeypatch.setattr(settings, "encryption_key", _TEST_KEK)


def _stored_file(uuid: str) -> Path:
    """The one file on disk for a media uuid."""
    media_dir = Path(os.environ["UPLOAD_DIR"]) / "media"
    found = sorted(media_dir.glob(f"{uuid}*"))
    assert len(found) == 1, found
    return found[0]


@pytest.mark.asyncio
@pytest.mark.usefixtures("kek")
@pytest.mark.requirement("media:R84")
async def test_should_store_encrypted_and_serve_original_when_upload_asks(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, access_roles=["self"], encrypt=True)
    assert record["isEncrypted"] is True

    stored = _stored_file(record["uuid"])
    assert stored.name.endswith(".enc")
    assert stored.read_bytes() != png_bytes()

    download = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download", headers=auth(alice)
    )
    assert download.status_code == 200, download.text
    assert download.content == png_bytes()
    assert download.headers["content-type"].split(";")[0] == record["mimeType"]

    read = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(alice))
    assert read.json()["isEncrypted"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("kek")
@pytest.mark.requirement("media:R85")
async def test_should_refuse_encrypted_video_upload(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": ("clip.mp4", mp4_bytes(), "video/mp4")},
        data={"encrypt": "true"},
        headers=auth(alice),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "ENCRYPTION_NOT_SUPPORTED_FOR_VIDEO"

    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert mine.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R86")
async def test_should_refuse_encrypted_upload_when_no_key_is_configured(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "encryption_key", None)
    alice = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": ("img.png", png_bytes(), "image/png")},
        data={"preserveOriginal": "true", "encrypt": "true"},
        headers=auth(alice),
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENCRYPTION_NOT_CONFIGURED"

    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert mine.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R86")
async def test_should_refuse_encrypted_download_when_key_is_removed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "encryption_key", _TEST_KEK)
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, encrypt=True)
    await db_session.commit()
    monkeypatch.setattr(settings, "encryption_key", None)

    response = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download", headers=auth(alice)
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENCRYPTION_NOT_CONFIGURED"


@pytest.mark.asyncio
@pytest.mark.usefixtures("kek")
@pytest.mark.requirement("media:R87")
async def test_should_refuse_encrypted_upload_over_encrypted_limit(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "max_encrypted_upload_size_mb", 0)
    alice = await create_member_user(db_session, "alice")
    plain = await upload(client, alice)
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": ("img.png", png_bytes(), "image/png")},
        data={"preserveOriginal": "true", "encrypt": "true"},
        headers=auth(alice),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "ENCRYPTED_FILE_TOO_LARGE"
    assert response.json()["detail"]["limitMb"] == 0

    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert [item["uuid"] for item in mine.json()["items"]] == [plain["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R92")
@pytest.mark.requirement("media:R94")
async def test_should_mark_plain_download_publicly_cacheable_for_a_day(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)

    download = await client.get(f"/v1/media/by_id/{record['uuid']}/download")
    assert download.status_code == 200
    assert download.headers["cache-control"] == "public, max-age=86400"
    assert download.headers["access-control-allow-origin"] == "*"


@pytest.mark.asyncio
@pytest.mark.usefixtures("kek")
@pytest.mark.requirement("media:R93")
@pytest.mark.requirement("media:R94")
async def test_should_mark_encrypted_download_private_and_unstored(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, access_roles=["self"], encrypt=True)

    download = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download", headers=auth(alice)
    )
    assert download.status_code == 200, download.text
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["access-control-allow-origin"] == "*"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R92")
async def test_should_not_mark_private_plain_download_publicly_cacheable(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, access_roles=["self"])

    download = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download", headers=auth(alice)
    )
    assert download.status_code == 200, download.text
    assert "public" not in download.headers["cache-control"]
    assert download.headers["cache-control"] == "private, max-age=86400"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R92")
async def test_should_mark_download_private_when_roles_admit_only_coaches(
    client: AsyncClient, db_session: AsyncSession
):
    coach = await create_coach_user(db_session, "carl")
    record = await upload(client, coach, access_roles=["coach"])

    download = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download", headers=auth(coach)
    )
    assert download.status_code == 200, download.text
    assert download.headers["cache-control"] == "private, max-age=86400"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R92")
async def test_should_mark_pdf_poster_private_when_media_is_not_public(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    record = await upload(
        client,
        alice,
        filename="doc.pdf",
        content=pdf_bytes(),
        content_type="application/pdf",
        access_roles=["self"],
        preserve=False,
    )

    poster = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download",
        params={"variant": "poster"},
        headers=auth(alice),
    )
    assert poster.status_code == 200, poster.text
    assert poster.headers["cache-control"] == "private, max-age=86400"
