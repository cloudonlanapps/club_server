"""Creating a media link requires that the caller may view the media (#463).

The create response embeds the media reference, and 201 against 404 would
confirm that a uuid exists, so media the caller cannot see is answered
exactly as media that does not exist.
"""

import json
import os
import shutil
import struct
import tempfile
import uuid
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    create_general_evaluation,
    create_template,
    item_ids,
)
from .helpers import create_admin_user, create_coach_user, create_member_user


def _png_bytes() -> bytes:
    """Smallest valid PNG the upload pipeline will accept."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = (
        struct.pack(">I", 13)
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    )
    comp = zlib.compress(b"\x00\xff\xff\xff")
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
    """Uploads land on disk; clear them between tests."""
    upload_dir = os.environ.get("UPLOAD_DIR", "/tmp/club_server_test_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    yield
    shutil.rmtree(upload_dir, ignore_errors=True)
    tmp_base = tempfile.gettempdir()
    for entry in os.listdir(tmp_base):
        if entry.startswith("club_media_") or entry.startswith("club_upload_"):
            shutil.rmtree(os.path.join(tmp_base, entry), ignore_errors=True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _upload(client: AsyncClient, token: str, access_roles: list[str]) -> str:
    response = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "image/png")},
        data={"preserveOriginal": "true", "accessRoles": json.dumps(access_roles)},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["uuid"]


# --- user links ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R58")
async def test_should_refuse_link_when_caller_cannot_view_media(
    client: AsyncClient, db_session: AsyncSession
):
    """A member cannot link another member's private media to their profile."""
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    bobs_private = await _upload(client, bob, ["self"])
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "gallery", "mediaUuid": bobs_private},
        headers=_auth(alice),
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "MEDIA_NOT_FOUND"
    assert bobs_private not in response.text

    listing = await client.get("/v1/users/by_id/alice/media", headers=_auth(alice))
    assert listing.status_code == 200
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R58")
async def test_should_answer_invisible_media_as_missing_media(
    client: AsyncClient, db_session: AsyncSession
):
    """The refusal is indistinguishable from a uuid that does not exist."""
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    bobs_private = await _upload(client, bob, ["self"])
    await db_session.commit()

    hidden = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "gallery", "mediaUuid": bobs_private},
        headers=_auth(alice),
    )
    missing = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "gallery", "mediaUuid": str(uuid.uuid4())},
        headers=_auth(alice),
    )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"] == missing.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R26")
async def test_should_create_link_when_caller_uploaded_private_media(
    client: AsyncClient, db_session: AsyncSession
):
    """The uploader still links their own private media."""
    alice = await create_member_user(db_session, "alice")
    own_private = await _upload(client, alice, ["self"])

    response = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "gallery", "mediaUuid": own_private},
        headers=_auth(alice),
    )
    assert response.status_code == 201, response.text
    assert response.json()["media"]["uuid"] == own_private

    listing = await client.get("/v1/users/by_id/alice/media", headers=_auth(alice))
    assert listing.status_code == 200
    assert [link["media"]["uuid"] for link in listing.json()["gallery"]] == [
        own_private
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R70")
async def test_should_create_link_when_admin_may_view_media(
    client: AsyncClient, db_session: AsyncSession
):
    """An admin links a member's self+admin media to that member."""
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    document = await _upload(client, alice, ["self", "admin"])

    response = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "identity_document", "mediaUuid": document},
        headers=_auth(admin),
    )
    assert response.status_code == 201, response.text

    listing = await client.get(
        "/v1/users/by_id/alice/media/identity_document", headers=_auth(admin)
    )
    assert listing.status_code == 200
    assert [link["media"]["uuid"] for link in listing.json()] == [document]


# --- evaluation links ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
@pytest.mark.requirement("media:R97")
async def test_should_refuse_evaluation_link_when_author_cannot_view_media(
    client: AsyncClient, db_session: AsyncSession
):
    """The evaluation attach route applies the same gate."""
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin)
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    [item_id] = await item_ids(client, admin, template_id)
    admin_only = await _upload(client, admin, ["admin"])
    await db_session.commit()

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/media",
        json={"tag": str(item_id), "mediaUuid": admin_only},
        headers=_auth(coach),
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "MEDIA_NOT_FOUND"

    listing = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}/media", headers=_auth(coach)
    )
    assert listing.status_code == 200
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
@pytest.mark.requirement("media:R27")
async def test_should_create_evaluation_link_when_author_may_view_media(
    client: AsyncClient, db_session: AsyncSession
):
    """The author links coach-visible media to their evaluation."""
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin)
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    [item_id] = await item_ids(client, admin, template_id)
    coach_visible = await _upload(client, admin, ["admin", "coach"])

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/media",
        json={"tag": str(item_id), "mediaUuid": coach_visible},
        headers=_auth(coach),
    )
    assert response.status_code == 201, response.text

    listing = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}/media", headers=_auth(coach)
    )
    assert listing.status_code == 200
    assert [link["media"]["uuid"] for link in listing.json()[str(item_id)]] == [
        coach_visible
    ]
