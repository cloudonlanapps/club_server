"""Upload, conversion, download and record rules of media_requirements.md (#494)."""

import asyncio
import shutil

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.audit_log import AuditLog
from club_server.db.models.media import Media

from .helpers import create_admin_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    mp4_bytes,
    pdf_bytes,
    png_bytes,
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")


@pytest.mark.asyncio
@pytest.mark.requirement("media:R2")
async def test_should_refuse_upload_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": ("img.png", png_bytes(), "image/png")},
        data={"preserveOriginal": "true"},
    )
    assert response.status_code == 401, response.text

    listing = await client.get("/v1/media", headers=auth(admin))
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R4")
async def test_should_refuse_file_over_its_media_type_limit(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "max_image_upload_size_mb", 0)
    admin = await create_admin_user(db_session)
    pdf = await upload(
        client,
        admin,
        filename="form.pdf",
        content=pdf_bytes(),
        content_type="application/pdf",
    )
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": ("img.png", png_bytes(), "image/png")},
        data={"preserveOriginal": "true"},
        headers=auth(admin),
    )
    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "FILE_TOO_LARGE"
    assert detail["mediaType"] == "image"
    assert detail["limitMb"] == 0

    listing = await client.get("/v1/media", headers=auth(admin))
    assert [item["uuid"] for item in listing.json()["items"]] == [pdf["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6")
async def test_should_accept_video_as_pending_when_uploaded(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await client.post(
        "/v1/media",
        files={"file": ("clip.mp4", mp4_bytes(), "video/mp4")},
        data={"preserveOriginal": "false"},
        headers=auth(admin),
    )
    assert response.status_code == 202, response.text
    assert response.json()["mediaType"] == "video"
    assert response.json()["conversionStatus"] == "pending"

    record = await client.get(
        f"/v1/media/by_id/{response.json()['id']}", headers=auth(admin)
    )
    assert record.status_code == 200
    assert record.json()["conversionStatus"] == "pending"


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")
@pytest.mark.requirement("media:R10")
async def test_should_serve_first_page_still_when_pdf_is_converted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(
        client,
        admin,
        filename="form.pdf",
        content=pdf_bytes(),
        content_type="application/pdf",
        preserve=False,
    )
    assert record["conversionStatus"] == "completed"

    poster = await client.get(
        f"/v1/media/by_id/{record['uuid']}/download?variant=poster"
    )
    assert poster.status_code == 200, poster.text
    assert poster.headers["content-type"].split(";")[0] == "image/png"
    assert poster.content.startswith(b"\x89PNG")

    original = await client.get(f"/v1/media/by_id/{record['uuid']}/download")
    assert original.status_code == 200
    assert original.headers["content-type"].split(";")[0] == "application/pdf"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R14")
async def test_should_answer_not_found_when_downloading_unknown_or_deleted_media(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    deleted = await client.delete(
        f"/v1/media/by_id/{record['id']}", headers=auth(admin)
    )
    assert deleted.status_code == 204
    await db_session.commit()

    gone = await client.get(f"/v1/media/by_id/{record['uuid']}/download")
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "MEDIA_NOT_FOUND"

    unknown = await client.get("/v1/media/by_id/no-such-uuid/download")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R20")
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        ("pending", 409, "CONVERSION_IN_PROGRESS"),
        ("processing", 409, "CONVERSION_IN_PROGRESS"),
        ("failed", 422, "CONVERSION_FAILED"),
    ],
)
async def test_should_refuse_download_when_video_is_not_converted(
    client: AsyncClient,
    db_session: AsyncSession,
    status: str,
    code: int,
    expected: str,
):
    admin = await create_admin_user(db_session)
    response = await client.post(
        "/v1/media",
        files={"file": ("clip.mp4", mp4_bytes(), "video/mp4")},
        data={"preserveOriginal": "false"},
        headers=auth(admin),
    )
    assert response.status_code == 202, response.text
    video = response.json()
    await db_session.execute(
        update(Media)
        .where(Media.uuid == video["uuid"])
        .values(conversion_status=status)
    )
    await db_session.commit()

    record = await client.get(f"/v1/media/by_id/{video['id']}", headers=auth(admin))
    assert record.json()["conversionStatus"] == status

    download = await client.get(f"/v1/media/by_id/{video['uuid']}/download")
    assert download.status_code == code, download.text
    assert download.json()["detail"]["code"] == expected


@pytest.mark.asyncio
@pytest.mark.requirement("media:R22")
async def test_should_refuse_empty_access_roles_on_upload_and_change(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    await db_session.commit()

    refused_upload = await client.post(
        "/v1/media",
        files={"file": ("img.png", png_bytes(), "image/png")},
        data={"preserveOriginal": "true", "accessRoles": "[]"},
        headers=auth(admin),
    )
    assert refused_upload.status_code == 422
    assert refused_upload.json()["detail"]["code"] == "INVALID_ACCESS_ROLES"

    refused_change = await client.patch(
        f"/v1/media/by_id/{record['id']}",
        json={"accessRoles": []},
        headers=auth(admin),
    )
    assert refused_change.status_code == 422
    assert refused_change.json()["detail"]["code"] == "INVALID_ACCESS_ROLES"

    after = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(admin))
    assert after.json()["accessRoles"] == ["public"]
    listing = await client.get("/v1/media", headers=auth(admin))
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("media:R23")
@pytest.mark.parametrize("raw", ['"public"', "not json", '{"roles": ["self"]}'])
async def test_should_refuse_upload_when_access_roles_are_not_a_list(
    client: AsyncClient, db_session: AsyncSession, raw: str
):
    member = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await client.post(
        "/v1/media",
        files={"file": ("img.png", png_bytes(), "image/png")},
        data={"preserveOriginal": "true", "accessRoles": raw},
        headers=auth(member),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_ACCESS_ROLES"

    mine = await client.get("/v1/media/myfiles", headers=auth(member))
    assert mine.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R24")
async def test_should_collapse_to_public_and_drop_repeats_when_storing_access_roles(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin, access_roles=["self", "public", "admin"])
    assert record["accessRoles"] == ["public"]

    changed = await client.patch(
        f"/v1/media/by_id/{record['id']}",
        json={"accessRoles": ["admin", "coach", "admin"]},
        headers=auth(admin),
    )
    assert changed.status_code == 200

    stored = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(admin))
    assert stored.json()["accessRoles"] == ["admin", "coach"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R35")
async def test_should_list_newest_first_and_narrow_by_type_and_status(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    image = await upload(client, admin)
    await asyncio.sleep(0.01)
    pdf = await upload(
        client,
        admin,
        filename="form.pdf",
        content=pdf_bytes(),
        content_type="application/pdf",
    )
    await asyncio.sleep(0.01)
    video = await client.post(
        "/v1/media",
        files={"file": ("clip.mp4", mp4_bytes(), "video/mp4")},
        headers=auth(admin),
    )
    assert video.status_code == 202

    everything = await client.get("/v1/media", headers=auth(admin))
    assert [item["uuid"] for item in everything.json()["items"]] == [
        video.json()["uuid"],
        pdf["uuid"],
        image["uuid"],
    ]

    pdfs = await client.get("/v1/media?mediaType=pdf", headers=auth(admin))
    assert [item["uuid"] for item in pdfs.json()["items"]] == [pdf["uuid"]]
    assert pdfs.json()["total"] == 1

    pending = await client.get(
        "/v1/media?conversionStatus=pending", headers=auth(admin)
    )
    assert [item["uuid"] for item in pending.json()["items"]] == [video.json()["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R54")
async def test_should_audit_every_media_mutation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        settings, "encryption_key", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    )
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    mid = record["id"]

    for roles in (["admin"], ["admin"]):  # the second change changes nothing
        changed = await client.patch(
            f"/v1/media/by_id/{mid}", json={"accessRoles": roles}, headers=auth(admin)
        )
        assert changed.status_code == 200
    for _ in range(2):  # the second encrypt is a no-op
        encrypted = await client.post(
            f"/v1/media/by_id/{record['uuid']}/encrypt", headers=auth(admin)
        )
        assert encrypted.status_code == 200
    steps = [
        ("DELETE", f"/v1/media/by_id/{mid}"),
        ("POST", f"/v1/media/by_id/{mid}/restore"),
        ("DELETE", f"/v1/media/by_id/{mid}"),
        ("DELETE", f"/v1/media/by_id/{mid}/hard"),
    ]
    for method, url in steps:
        response = await client.request(method, url, headers=auth(admin))
        assert response.status_code in (200, 204), response.text

    actions = (
        (
            await db_session.execute(
                select(AuditLog.action)
                .where(
                    AuditLog.resource_type == "media", AuditLog.resource_id == str(mid)
                )
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    assert actions == [
        "upload_media_v2",
        "update_media_v2",
        "encrypt_media_v2",
        "soft_delete_media_v2",
        "restore_media_v2",
        "soft_delete_media_v2",
        "hard_delete_media_v2",
    ]
