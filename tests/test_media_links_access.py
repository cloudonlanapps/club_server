"""Tests for #167 — media-link list endpoints filter rows by media.access_roles."""

import json
import os
import shutil
import struct
import tempfile
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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


async def _upload(
    client: AsyncClient,
    token: str,
    access_roles: list[str] | None = None,
) -> dict:
    data: dict[str, str] = {"preserveOriginal": "true"}
    if access_roles is not None:
        data["accessRoles"] = json.dumps(access_roles)
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "image/png")},
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _attach_to_alice(
    client: AsyncClient,
    alice_token: str,
    media_uuid: str,
    tag: str,
) -> None:
    r = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": tag, "mediaUuid": media_uuid},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 201, r.text


# ---------- per-user link visibility ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R28")
@pytest.mark.requirement("media:R67")
async def test_self_admin_link_hidden_from_coach_via_list_grouped(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Coach passes the route guard but must NOT see self+admin link rows."""
    alice = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")

    m = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, m["uuid"], "identity_document")

    r = await client.get(
        "/v1/users/by_id/alice/media",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r.status_code == 200
    assert r.json() == {}, "coach must not see admin-only link rows"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R28")
@pytest.mark.requirement("media:R67")
async def test_self_admin_link_hidden_from_coach_via_list_by_tag(
    client: AsyncClient,
    db_session: AsyncSession,
):
    alice = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")

    m = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, m["uuid"], "identity_document")

    r = await client.get(
        "/v1/users/by_id/alice/media/identity_document",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
@pytest.mark.requirement("media:R67")
async def test_self_admin_link_returns_404_to_coach_via_get_one(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Per-row GET must 404 (not 200) to a viewer that lacks access — no
    existence disclosure (#167)."""
    alice = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")

    m = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, m["uuid"], "identity_document")

    r = await client.get(
        f"/v1/users/by_id/alice/media/identity_document/{m['uuid']}",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("media:R26")
async def test_self_can_see_own_self_admin_link(
    client: AsyncClient,
    db_session: AsyncSession,
):
    alice = await create_registered_user(db_session, "alice")
    m = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, m["uuid"], "identity_document")

    r = await client.get(
        "/v1/users/by_id/alice/media/identity_document",
        headers={"Authorization": f"Bearer {alice}"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["media"]["uuid"] == m["uuid"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R70")
async def test_admin_can_see_self_admin_link(
    client: AsyncClient,
    db_session: AsyncSession,
):
    admin = await create_admin_user(db_session)
    alice = await create_registered_user(db_session, "alice")
    m = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, m["uuid"], "identity_document")

    r = await client.get(
        "/v1/users/by_id/alice/media/identity_document",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("media:R25")
@pytest.mark.requirement("media:R70")
async def test_public_link_visible_to_all_roles_no_regression(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Avatar (public) must remain visible to every role — baseline (#167)."""
    alice = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")
    admin = await create_admin_user(db_session)

    m = await _upload(client, alice)  # default = ["public"]
    await _attach_to_alice(client, alice, m["uuid"], "avatar")

    for tok in (alice, coach_token, admin):
        r = await client.get(
            "/v1/users/by_id/alice/media/avatar",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
        assert len(r.json()) == 1


# ---------- mixed visibility within one owner ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R67")
async def test_mixed_visibility_returns_only_visible_rows(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Owner with one public + one self+admin link: coach sees only public."""
    alice = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")

    pub = await _upload(client, alice)
    priv = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, pub["uuid"], "avatar")
    await _attach_to_alice(client, alice, priv["uuid"], "identity_document")

    r = await client.get(
        "/v1/users/by_id/alice/media",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r.status_code == 200
    grouped = r.json()
    assert list(grouped.keys()) == ["avatar"]
    assert len(grouped["avatar"]) == 1


# ---------- cross-owner search (admin/coach) ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R83")
async def test_cross_owner_search_filters_for_coach(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """`/v1/media/links` already gates entry to admin/coach. The per-row
    filter must additionally hide admin-only rows from a non-admin coach (#167)."""
    admin = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "thecoach")
    alice = await create_registered_user(db_session, "alice")

    pub = await _upload(client, alice)
    priv = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, pub["uuid"], "avatar")
    await _attach_to_alice(client, alice, priv["uuid"], "identity_document")

    r_coach = await client.get(
        "/v1/media/links",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r_coach.status_code == 200
    coach_uuids = {item["mediaUuid"] for item in r_coach.json()["items"]}
    assert pub["uuid"] in coach_uuids
    assert priv["uuid"] not in coach_uuids
    assert r_coach.json()["total"] == 1

    r_admin = await client.get(
        "/v1/media/links",
        headers={"Authorization": f"Bearer {admin}"},
    )
    admin_uuids = {item["mediaUuid"] for item in r_admin.json()["items"]}
    assert pub["uuid"] in admin_uuids
    assert priv["uuid"] in admin_uuids


# ---------- reverse lookup ----------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R79")
async def test_reverse_lookup_hides_links_for_inaccessible_media(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """`/v1/media/by_id/{uuid}/links` returns [] to a viewer who cannot see
    the media itself, even though the link rows exist."""
    alice = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")

    m = await _upload(client, alice, access_roles=["self", "admin"])
    await _attach_to_alice(client, alice, m["uuid"], "identity_document")

    r = await client.get(
        f"/v1/media/by_id/{m['uuid']}/links",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r.status_code == 200
    assert r.json() == []
