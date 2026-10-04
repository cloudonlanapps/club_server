"""Access-role, record and deletion rules of media_requirements.md (#494)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import User

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
)
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    png_bytes,
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")


@pytest.mark.asyncio
@pytest.mark.requirement("media:R29")
async def test_should_let_super_admin_view_media_when_roles_do_not_list_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    private = await upload(client, alice, access_roles=["self"])

    download = await client.get(
        f"/v1/media/by_id/{private['uuid']}/download", headers=auth(admin)
    )
    assert download.status_code == 200, download.text
    assert download.content == png_bytes()

    links = await client.get(
        f"/v1/media/by_id/{private['uuid']}/links", headers=auth(admin)
    )
    assert links.status_code == 200
    assert links.json() == []


@pytest.mark.asyncio
@pytest.mark.requirement("media:R31")
async def test_should_forbid_download_when_logged_in_caller_is_not_admitted(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    private = await upload(client, alice, access_roles=["self"])
    await db_session.commit()

    refused = await client.get(
        f"/v1/media/by_id/{private['uuid']}/download", headers=auth(bob)
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["code"] == "FORBIDDEN"

    owner = await client.get(
        f"/v1/media/by_id/{private['uuid']}/download", headers=auth(alice)
    )
    assert owner.status_code == 200
    assert owner.content == png_bytes()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R33")
@pytest.mark.parametrize("status", ["blocked", "left"])
async def test_should_treat_blocked_or_departed_user_as_anonymous_on_download(
    client: AsyncClient, db_session: AsyncSession, status: str
):
    coach = await create_coach_user(db_session, "cora")
    admin = await create_admin_user(db_session)
    staff_only = await upload(client, admin, access_roles=["coach"])

    before = await client.get(
        f"/v1/media/by_id/{staff_only['uuid']}/download", headers=auth(coach)
    )
    assert before.status_code == 200

    await db_session.execute(
        update(User).where(User.username == "cora").values(status=status)
    )
    await db_session.commit()

    after = await client.get(
        f"/v1/media/by_id/{staff_only['uuid']}/download", headers=auth(coach)
    )
    assert after.status_code == 401, after.text
    assert after.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40")
async def test_should_let_coach_read_record_when_roles_do_not_admit_them(
    client: AsyncClient, db_session: AsyncSession
):
    coach = await create_coach_user(db_session, "cora")
    alice = await create_member_user(db_session, "alice")
    private = await upload(client, alice, access_roles=["self"])

    download = await client.get(
        f"/v1/media/by_id/{private['uuid']}/download", headers=auth(coach)
    )
    assert download.status_code == 403

    record = await client.get(f"/v1/media/by_id/{private['id']}", headers=auth(coach))
    assert record.status_code == 200, record.text
    assert record.json()["uuid"] == private["uuid"]
    assert record.json()["accessRoles"] == ["self"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40")
async def test_should_read_record_when_media_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    deleted = await client.delete(
        f"/v1/media/by_id/{record['id']}", headers=auth(admin)
    )
    assert deleted.status_code == 204

    read = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(admin))
    assert read.status_code == 200, read.text
    assert read.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R42")
@pytest.mark.requirement("media:R69")
async def test_should_keep_media_without_uploader_when_user_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    avatar = await upload(client, alice)
    linked = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": avatar["uuid"]},
        headers=auth(alice),
    )
    assert linked.status_code == 201

    soft = await client.delete("/v1/users/by_id/alice", headers=auth(admin))
    assert soft.status_code == 200, soft.text
    hard = await client.delete("/v1/users/by_id/alice/hard", headers=auth(admin))
    assert hard.status_code == 204, hard.text

    record = await client.get(f"/v1/media/by_id/{avatar['id']}", headers=auth(admin))
    assert record.status_code == 200, record.text
    assert record.json()["uploadedBy"] is None
    assert record.json()["deletedAtUtc"] is None

    links = await client.get(
        f"/v1/media/by_id/{avatar['uuid']}/links", headers=auth(admin)
    )
    assert links.json() == []


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69")
async def test_should_remove_links_but_keep_media_when_venue_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(admin))
    venue_id = venue.json()["id"]
    image = await upload(client, admin)
    linked = await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "venue_image", "mediaUuid": image["uuid"]},
        headers=auth(admin),
    )
    assert linked.status_code == 201

    soft = await client.delete(f"/v1/venues/by_id/{venue_id}", headers=auth(admin))
    assert soft.status_code == 200, soft.text
    hard = await client.delete(f"/v1/venues/by_id/{venue_id}/hard", headers=auth(admin))
    assert hard.status_code == 204, hard.text

    links = await client.get(
        f"/v1/media/by_id/{image['uuid']}/links", headers=auth(admin)
    )
    assert links.json() == []
    download = await client.get(f"/v1/media/by_id/{image['uuid']}/download")
    assert download.status_code == 200
    assert download.content == png_bytes()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R46")
async def test_should_refuse_member_changing_another_users_public_media(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    public = await upload(client, alice)
    await db_session.commit()

    changed = await client.patch(
        f"/v1/media/by_id/{public['id']}",
        json={"accessRoles": ["self"]},
        headers=auth(bob),
    )
    deleted = await client.delete(f"/v1/media/by_id/{public['id']}", headers=auth(bob))

    record = await client.get(f"/v1/media/by_id/{public['id']}", headers=auth(alice))
    assert changed.status_code == 403, changed.text
    assert changed.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"
    assert deleted.status_code == 403, deleted.text
    assert deleted.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"
    assert record.json()["accessRoles"] == ["public"]
    assert record.json()["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R50")
async def test_should_refuse_without_change_when_restoring_live_media(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)

    restored = await client.post(
        f"/v1/media/by_id/{record['id']}/restore", headers=auth(admin)
    )
    assert restored.status_code == 422, restored.text
    assert restored.json()["detail"]["code"] == "NOTHING_TO_RESTORE"

    listing = await client.get("/v1/media", headers=auth(admin))
    assert [item["uuid"] for item in listing.json()["items"]] == [record["uuid"]]
