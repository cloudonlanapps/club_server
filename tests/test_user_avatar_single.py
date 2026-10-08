"""A user has one avatar (#28, media R69a–R69e).

Replacing a profile photo was four client calls, the first a listing that
leaves out links the caller may not view: an admin replacing a member's
private photo saw nothing to remove, and the old link and file stayed.
The server keeps the rule itself, when the new avatar is linked.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.constants import USER_AVATAR_TAG
from club_server.db.models.audit_log import AuditLog

from .helpers import (
    create_admin_user,
    create_member_user,
    create_regular_admin_user,
)
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    png_bytes,
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

PRIVATE_ROLES = ["self", "admin", "coach"]
UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"


async def link(
    client: AsyncClient,
    token: str,
    owner_path: str,
    media_uuid: str,
    tag: str = USER_AVATAR_TAG,
    expect: int = 201,
) -> dict:
    """Link a media item to the owner at ``owner_path`` under ``tag``."""
    response = await client.post(
        f"/v1/{owner_path}/media",
        json={"tag": tag, "mediaUuid": media_uuid},
        headers=auth(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


async def linked_uuids(
    client: AsyncClient, token: str, owner_path: str, tag: str = USER_AVATAR_TAG
) -> list[str]:
    """Uuids linked to the owner under ``tag``, as ``token`` sees them."""
    response = await client.get(f"/v1/{owner_path}/media/{tag}", headers=auth(token))
    assert response.status_code == 200, response.text
    return [row["media"]["uuid"] for row in response.json()]


async def deleted_at(client: AsyncClient, token: str, media_id: int) -> int | None:
    response = await client.get(f"/v1/media/by_id/{media_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["deletedAtUtc"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69a")
async def test_should_leave_one_avatar_when_a_member_links_a_new_one(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    sudo = await create_admin_user(db_session)
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", old["uuid"])
    assert await linked_uuids(client, alice, "users/by_id/alice") == [old["uuid"]]

    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", new["uuid"])

    assert await linked_uuids(client, alice, "users/by_id/alice") == [new["uuid"]]
    assert await linked_uuids(client, sudo, "users/by_id/alice") == [new["uuid"]]
    assert await deleted_at(client, alice, old["id"]) is not None
    assert await deleted_at(client, alice, new["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69a")
async def test_should_remove_an_avatar_the_admin_cannot_view_when_the_admin_links_a_new_one(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    admin = await create_regular_admin_user(db_session, "boss")
    sudo = await create_admin_user(db_session)
    old = await upload(client, alice, access_roles=["self"])
    await link(client, alice, "users/by_id/alice", old["uuid"])
    assert await linked_uuids(client, admin, "users/by_id/alice") == []
    assert await linked_uuids(client, sudo, "users/by_id/alice") == [old["uuid"]]

    response = await client.post(
        "/v1/media",
        files={"file": ("photo.png", png_bytes(), "image/png")},
        data={
            "preserveOriginal": "true",
            "ownerUsername": "alice",
            "accessRoles": '["self", "admin", "coach"]',
        },
        headers=auth(admin),
    )
    assert response.status_code == 201, response.text
    new = response.json()
    await link(client, admin, "users/by_id/alice", new["uuid"])

    assert await linked_uuids(client, sudo, "users/by_id/alice") == [new["uuid"]]
    assert await linked_uuids(client, alice, "users/by_id/alice") == [new["uuid"]]
    assert await deleted_at(client, alice, old["id"]) is not None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69b")
async def test_should_keep_a_replaced_avatar_file_when_another_link_uses_it(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", old["uuid"])
    await link(client, alice, "users/by_id/alice", old["uuid"], tag="gallery")

    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", new["uuid"])

    assert await linked_uuids(client, alice, "users/by_id/alice") == [new["uuid"]]
    assert await linked_uuids(client, alice, "users/by_id/alice", "gallery") == [
        old["uuid"]
    ]
    assert await deleted_at(client, alice, old["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69c")
async def test_should_keep_the_current_avatar_when_the_new_item_is_unknown(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", old["uuid"])

    refused = await link(client, alice, "users/by_id/alice", UNKNOWN_UUID, expect=404)
    assert refused["detail"]["code"] == "MEDIA_NOT_FOUND"

    assert await linked_uuids(client, alice, "users/by_id/alice") == [old["uuid"]]
    assert await deleted_at(client, alice, old["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69c")
async def test_should_keep_the_current_avatar_when_it_is_linked_again(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", old["uuid"])

    refused = await link(client, alice, "users/by_id/alice", old["uuid"], expect=409)
    assert refused["detail"]["code"] == "MEDIA_LINK_EXISTS"

    assert await linked_uuids(client, alice, "users/by_id/alice") == [old["uuid"]]
    assert await deleted_at(client, alice, old["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69d")
async def test_should_keep_every_link_when_a_user_links_under_another_tag(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    first = await upload(client, alice, access_roles=PRIVATE_ROLES)
    second = await upload(client, alice, access_roles=PRIVATE_ROLES)

    await link(client, alice, "users/by_id/alice", first["uuid"], tag="gallery")
    await link(client, alice, "users/by_id/alice", second["uuid"], tag="gallery")

    assert await linked_uuids(client, alice, "users/by_id/alice", "gallery") == [
        first["uuid"],
        second["uuid"],
    ]
    assert await deleted_at(client, alice, first["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69d")
async def test_should_keep_every_link_when_a_venue_links_under_the_avatar_tag(
    client: AsyncClient, db_session: AsyncSession
):
    sudo = await create_admin_user(db_session)
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(sudo))
    assert venue.status_code == 201, venue.text
    owner = f"venues/by_id/{venue.json()['id']}"
    first = await upload(client, sudo)
    second = await upload(client, sudo)

    await link(client, sudo, owner, first["uuid"])
    await link(client, sudo, owner, second["uuid"])

    assert await linked_uuids(client, sudo, owner) == [first["uuid"], second["uuid"]]
    assert await deleted_at(client, sudo, first["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69a")
async def test_should_link_a_new_avatar_when_the_tag_limit_is_reached(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "media_max_links_per_owner_tag", 1)
    alice = await create_member_user(db_session, "alice")
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", old["uuid"])

    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", new["uuid"])

    assert await linked_uuids(client, alice, "users/by_id/alice") == [new["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69e")
async def test_should_name_the_replaced_avatar_in_the_audit_row_of_the_link(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", old["uuid"])
    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, "users/by_id/alice", new["uuid"])

    rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.action == "create_user_media_link")
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    first, second = (json.loads(row.details) for row in rows)
    assert "replacedMediaUuids" not in first
    assert second["mediaUuid"] == new["uuid"]
    assert second["replacedMediaUuids"] == [old["uuid"]]
    assert second["deletedMediaUuids"] == [old["uuid"]]
