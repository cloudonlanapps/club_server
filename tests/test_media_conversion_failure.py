"""An image or PDF the converter cannot process (#521, media:R10a).

The upload answers 422 `MEDIA_CONVERSION_FAILED`, creates no media item and
leaves nothing behind, neither in the media store nor in the staging
directory. A PDF whose first page cannot be rendered counts as a failure
even when the renderer reports success. These drive the real converters,
as the other conversion tests do.
"""

import os
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

CORRUPT_IMAGE = b"\x89PNG\r\n\x1a\n" + b"this is not image data" * 20
NO_PDF_HEADER = b"this file has no PDF header at all\n" * 20
PDF_HEADER_OVER_GARBAGE = b"%PDF-1.4\n" + bytes(range(256)) * 8


@pytest.fixture
def staging_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage uploads in a directory of this test's own, so leftovers show."""
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(staging))
    return staging


async def _assert_nothing_created(
    client: AsyncClient, admin: str, member: str, staging: Path
) -> None:
    everything = await client.get(
        "/v1/media", params={"includeDeleted": "true"}, headers=auth(admin)
    )
    assert everything.status_code == 200, everything.text
    assert everything.json()["items"] == []
    mine = await client.get("/v1/media/myfiles", headers=auth(member))
    assert mine.status_code == 200, mine.text
    assert mine.json()["items"] == []
    media_dir = Path(os.environ["UPLOAD_DIR"]) / "media"
    stored = (
        [p for p in media_dir.rglob("*") if p.is_file()] if media_dir.exists() else []
    )
    assert stored == []
    assert list(staging.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.requirement("media:R10a")
@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("broken.png", CORRUPT_IMAGE, "image/png"),
        ("broken.pdf", NO_PDF_HEADER, "application/pdf"),
        ("broken.pdf", PDF_HEADER_OVER_GARBAGE, "application/pdf"),
    ],
    ids=["corrupt-image", "no-pdf-header", "pdf-header-over-garbage"],
)
async def test_should_refuse_upload_when_converter_cannot_process_file(
    client: AsyncClient,
    db_session: AsyncSession,
    staging_dir: Path,
    filename: str,
    content: bytes,
    content_type: str,
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mia")
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": (filename, content, content_type)},
        data={"preserveOriginal": "false"},
        headers=auth(member),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "MEDIA_CONVERSION_FAILED"
    await _assert_nothing_created(client, admin, member, staging_dir)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R10a")
async def test_should_keep_file_as_sent_when_preserving_a_file_it_cannot_convert(
    client: AsyncClient, db_session: AsyncSession
):
    member = await create_member_user(db_session, "mia")

    record = await upload(
        client,
        member,
        filename="broken.pdf",
        content=PDF_HEADER_OVER_GARBAGE,
        content_type="application/pdf",
        preserve=True,
    )

    assert record["conversionStatus"] == "none"
    download = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download", headers=auth(member)
    )
    assert download.status_code == 200, download.text
    assert download.content == PDF_HEADER_OVER_GARBAGE
