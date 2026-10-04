"""Tests for #181: media mutation endpoints honour per-media access_roles.

Covers soft-delete, restore, and access_roles update. The view gate
(``can_view_media``) has long required access_roles; these tests pin down
that the same gate now also applies to mutations.

Cases per endpoint:
- a coach who is NOT in ``access_roles`` is rejected (404),
- an admin is allowed whatever ``access_roles`` says (#503),
- owner is still allowed,
- super-admin is still allowed.
"""

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


async def _upload_as(
    client: AsyncClient,
    token: str,
    *,
    access_roles: str,
) -> dict:
    r = await client.post(
        "/v1/media",
        files={"file": ("img.png", _png_bytes(), "image/png")},
        data={"preserveOriginal": "true", "accessRoles": access_roles},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_soft_delete_admin_not_in_access_roles_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    admin_token = await create_regular_admin_user(db_session, "boss")
    body = await _upload_as(client, owner_token, access_roles='["self"]')

    r = await client.delete(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 204


@pytest.mark.asyncio
@pytest.mark.requirement("media:R45")
async def test_soft_delete_coach_not_in_access_roles_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    coach_token = await create_coach_user(db_session, "thecoach")
    body = await _upload_as(client, owner_token, access_roles='["self","admin"]')

    r = await client.delete(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_soft_delete_owner_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    body = await _upload_as(client, owner_token, access_roles='["self"]')

    r = await client.delete(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 204


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_soft_delete_super_admin_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    super_token = await create_admin_user(db_session)
    body = await _upload_as(client, owner_token, access_roles='["self"]')

    r = await client.delete(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 204


@pytest.mark.asyncio
@pytest.mark.requirement("media:R27")
@pytest.mark.requirement("media:R43")
async def test_soft_delete_admin_listed_in_access_roles_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    admin_token = await create_regular_admin_user(db_session, "boss")
    body = await _upload_as(client, owner_token, access_roles='["self","admin"]')

    r = await client.delete(
        f"/v1/media/by_id/{body['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_restore_admin_not_in_access_roles_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    admin_token = await create_regular_admin_user(db_session, "boss")
    body = await _upload_as(client, owner_token, access_roles='["self"]')
    mid = body["id"]
    r_del = await client.delete(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r_del.status_code == 204

    r = await client.post(
        f"/v1/media/by_id/{mid}/restore",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_restore_owner_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    body = await _upload_as(client, owner_token, access_roles='["self"]')
    mid = body["id"]
    await client.delete(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    r = await client.post(
        f"/v1/media/by_id/{mid}/restore",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_restore_super_admin_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    super_token = await create_admin_user(db_session)
    body = await _upload_as(client, owner_token, access_roles='["self"]')
    mid = body["id"]
    await client.delete(
        f"/v1/media/by_id/{mid}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    r = await client.post(
        f"/v1/media/by_id/{mid}/restore",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# PATCH access_roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_patch_access_roles_admin_not_in_access_roles_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    admin_token = await create_regular_admin_user(db_session, "boss")
    body = await _upload_as(client, owner_token, access_roles='["self"]')

    r = await client.patch(
        f"/v1/media/by_id/{body['id']}",
        json={"accessRoles": ["public"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_patch_access_roles_owner_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    body = await _upload_as(client, owner_token, access_roles='["self"]')

    r = await client.patch(
        f"/v1/media/by_id/{body['id']}",
        json={"accessRoles": ["public"]},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 200
    assert r.json()["accessRoles"] == ["public"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_patch_access_roles_super_admin_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
):
    owner_token = await create_registered_user(db_session, "alice")
    super_token = await create_admin_user(db_session)
    body = await _upload_as(client, owner_token, access_roles='["self"]')

    r = await client.patch(
        f"/v1/media/by_id/{body['id']}",
        json={"accessRoles": ["public"]},
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert r.status_code == 200
    assert r.json()["accessRoles"] == ["public"]
