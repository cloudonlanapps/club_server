"""Tests for #162 — per-owner media link tables, cross-owner queries,
and the MEDIA_IN_USE soft-delete guard.
"""

import os
import shutil
import struct
import tempfile
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog

from .helpers import (
    create_admin_user,
    create_coach_user,
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


async def _upload(client: AsyncClient, token: str) -> dict:
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "image/png")},
        data={"preserveOriginal": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _create_venue(client: AsyncClient, token: str, name: str = "V") -> int:
    r = await client.post(
        "/v1/venues",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_group(client: AsyncClient, token: str, name: str = "G") -> int:
    r = await client.post(
        "/v1/groups",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_event(client: AsyncClient, token: str, venue_id: int) -> int:
    from datetime import datetime, timedelta, timezone

    start = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp() * 1000)
    end = int((datetime.now(timezone.utc) + timedelta(hours=26)).timestamp() * 1000)
    r = await client.post(
        "/v1/events",
        json={
            "title": "E",
            "type": "oneOff",
            "visibility": "public",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": end,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------- user_media ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R55")
@pytest.mark.requirement("media:R57")
@pytest.mark.requirement("media:R63")
@pytest.mark.requirement("media:R64")
@pytest.mark.requirement("media:R70")
async def test_user_link_crud(client: AsyncClient, db_session: AsyncSession):
    alice = await create_registered_user(db_session, "alice")
    m = await _upload(client, alice)

    r = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": m["uuid"], "metadata": "primary"},
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r.status_code == 201
    assert r.json()["tag"] == "avatar"
    assert r.json()["metadata"] == "primary"

    # Duplicate insert → 409
    r2 = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "MEDIA_LINK_EXISTS"

    # List grouped
    r3 = await client.get(
        "/v1/users/by_id/alice/media",
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r3.status_code == 200
    assert "avatar" in r3.json()
    assert len(r3.json()["avatar"]) == 1

    # PATCH metadata
    r4 = await client.patch(
        f"/v1/users/by_id/alice/media/avatar/{m['uuid']}",
        json={"metadata": "updated"},
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r4.status_code == 200
    assert r4.json()["metadata"] == "updated"

    # DELETE link
    r5 = await client.delete(
        f"/v1/users/by_id/alice/media/avatar/{m['uuid']}",
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r5.status_code == 204

    r6 = await client.get(
        "/v1/users/by_id/alice/media",
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r6.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R71")
async def test_user_link_cross_user_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
):
    alice = await create_registered_user(db_session, "alice")
    bob = await create_registered_user(db_session, "bob")
    m = await _upload(client, alice)
    r = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {bob}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("media:R60")
async def test_user_link_unknown_owner_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    r = await client.get(
        "/v1/users/by_id/nope/media",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R58")
async def test_user_link_invalid_media_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    r = await client.post(
        "/v1/users/by_id/admin/media",
        json={"tag": "avatar", "mediaUuid": "not-a-real-uuid"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


# ---------- venue_media (integer-owner exemplar; events/groups share the same factory shape) ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R55")
@pytest.mark.requirement("media:R66")
async def test_venue_link_crud(client: AsyncClient, db_session: AsyncSession):
    admin = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin, "Rink A")
    m = await _upload(client, admin)

    r = await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 201

    r2 = await client.get(
        f"/v1/venues/by_id/{venue_id}/media/banner",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    row = r2.json()[0]
    # The link says what it links; the media it points at is the same
    # descriptor every other projection embeds (#426).
    assert row["tag"] == "banner"
    assert row["media"] == {
        "uuid": m["uuid"],
        "mimeType": m["mimeType"],
        "filename": m["filename"],
    }


@pytest.mark.asyncio
@pytest.mark.requirement("media:R75")
async def test_venue_link_member_cannot_write(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    member = await create_registered_user(db_session, "alice")
    venue_id = await _create_venue(client, admin)
    m = await _upload(client, admin)
    r = await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {member}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("media:R55")
@pytest.mark.requirement("media:R66")
async def test_event_link_crud(client: AsyncClient, db_session: AsyncSession):
    admin = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin)
    event_id = await _create_event(client, admin, venue_id)
    m = await _upload(client, admin)
    r = await client.post(
        f"/v1/events/by_id/{event_id}/media",
        json={"tag": "poster", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 201

    r2 = await client.get(
        f"/v1/events/by_id/{event_id}/media",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r2.status_code == 200
    assert list(r2.json().keys()) == ["poster"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R55")
@pytest.mark.requirement("media:R66")
async def test_group_link_crud(client: AsyncClient, db_session: AsyncSession):
    admin = await create_admin_user(db_session)
    group_id = await _create_group(client, admin)
    m = await _upload(client, admin)
    r = await client.post(
        f"/v1/groups/by_id/{group_id}/media",
        json={"tag": "logo", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 201
    r2 = await client.get(
        f"/v1/groups/by_id/{group_id}/media/logo/{m['uuid']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r2.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("media:R64")
async def test_delete_tag_bulk(client: AsyncClient, db_session: AsyncSession):
    admin = await create_admin_user(db_session)
    m1 = await _upload(client, admin)
    m2 = await _upload(client, admin)
    venue_id = await _create_venue(client, admin)
    for m in (m1, m2):
        r = await client.post(
            f"/v1/venues/by_id/{venue_id}/media",
            json={"tag": "gallery", "mediaUuid": m["uuid"]},
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 201

    r = await client.delete(
        f"/v1/venues/by_id/{venue_id}/media/gallery",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 204

    r2 = await client.get(
        f"/v1/venues/by_id/{venue_id}/media",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r2.json() == {}


# ---------- soft-delete blocked when linked ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R47")
@pytest.mark.requirement("media:R64")
async def test_soft_delete_blocked_when_linked(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    m = await _upload(client, admin)
    venue_id = await _create_venue(client, admin)
    await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )

    r = await client.delete(
        f"/v1/media/by_id/{m['id']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "MEDIA_IN_USE"
    assert len(body["links"]) == 1
    assert body["links"][0]["ownerType"] == "venue"

    # Unlink, then soft-delete succeeds.
    await client.delete(
        f"/v1/venues/by_id/{venue_id}/media/banner/{m['uuid']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    r2 = await client.delete(
        f"/v1/media/by_id/{m['id']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r2.status_code == 204


# ---------- reverse lookup ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R55")
@pytest.mark.requirement("media:R78")
async def test_reverse_lookup_lists_all_links(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    m = await _upload(client, admin)
    venue_id = await _create_venue(client, admin)
    group_id = await _create_group(client, admin)

    await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    await client.post(
        f"/v1/groups/by_id/{group_id}/media",
        json={"tag": "logo", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )

    r = await client.get(
        f"/v1/media/by_id/{m['uuid']}/links",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    links = r.json()
    assert len(links) == 2
    owner_types = sorted(item["ownerType"] for item in links)
    assert owner_types == ["group", "venue"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R78")
async def test_reverse_lookup_orphan_empty(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    m = await _upload(client, admin)
    r = await client.get(
        f"/v1/media/by_id/{m['uuid']}/links",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.json() == []


# ---------- cross-owner search ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R80")
@pytest.mark.requirement("media:R82")
async def test_cross_owner_search(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "thecoach")
    m1 = await _upload(client, admin)
    m2 = await _upload(client, admin)
    venue_id = await _create_venue(client, admin)
    group_id = await _create_group(client, admin)
    await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": m1["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    await client.post(
        f"/v1/groups/by_id/{group_id}/media",
        json={"tag": "logo", "mediaUuid": m2["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )

    r = await client.get(
        "/v1/media/links",
        headers={"Authorization": f"Bearer {coach}"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r2 = await client.get(
        "/v1/media/links",
        params={"ownerType": "venue"},
        headers={"Authorization": f"Bearer {coach}"},
    )
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["ownerType"] == "venue"

    r3 = await client.get(
        "/v1/media/links",
        params={"ownerType": "nonsense"},
        headers={"Authorization": f"Bearer {coach}"},
    )
    assert r3.status_code == 422
    assert r3.json()["detail"]["code"] == "INVALID_OWNER_TYPE"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R82")
async def test_cross_owner_search_member_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
):
    member = await create_registered_user(db_session, "alice")
    r = await client.get(
        "/v1/media/links",
        headers={"Authorization": f"Bearer {member}"},
    )
    assert r.status_code == 403


# ---------- limits ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R62")
async def test_tag_full_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
):
    from club_server.config import settings

    monkeypatch.setattr(settings, "media_max_links_per_owner_tag", 2)

    admin = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin)
    m1 = await _upload(client, admin)
    m2 = await _upload(client, admin)
    m3 = await _upload(client, admin)

    for m in (m1, m2):
        r = await client.post(
            f"/v1/venues/by_id/{venue_id}/media",
            json={"tag": "g", "mediaUuid": m["uuid"]},
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 201

    r = await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "g", "mediaUuid": m3["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "MEDIA_LINK_TAG_FULL"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R62")
async def test_too_many_tags_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
):
    from club_server.config import settings

    monkeypatch.setattr(settings, "media_max_tags_per_owner", 2)

    admin = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin)
    m1 = await _upload(client, admin)
    m2 = await _upload(client, admin)
    m3 = await _upload(client, admin)

    for mu, tag in ((m1, "t1"), (m2, "t2")):
        r = await client.post(
            f"/v1/venues/by_id/{venue_id}/media",
            json={"tag": tag, "mediaUuid": mu["uuid"]},
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r.status_code == 201

    r = await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "t3", "mediaUuid": m3["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "MEDIA_LINK_TOO_MANY_TAGS"


# ---------- audit log ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R68")
async def test_audit_log_entries_written(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin)
    m = await _upload(client, admin)
    await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": m["uuid"]},
        headers={"Authorization": f"Bearer {admin}"},
    )
    await client.patch(
        f"/v1/venues/by_id/{venue_id}/media/banner/{m['uuid']}",
        json={"metadata": "hello"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    await client.delete(
        f"/v1/venues/by_id/{venue_id}/media/banner/{m['uuid']}",
        headers={"Authorization": f"Bearer {admin}"},
    )

    rows = (
        (
            await db_session.execute(
                select(AuditLog.action).where(
                    AuditLog.action.in_(
                        [
                            "create_venue_media_link",
                            "update_venue_media_link",
                            "delete_venue_media_link",
                        ]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(rows) == {
        "create_venue_media_link",
        "update_venue_media_link",
        "delete_venue_media_link",
    }
