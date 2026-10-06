"""An admin uploads media on behalf of a user (#18, media R6a–R6d).

An admin sets a member's profile photo. The link side already allowed it;
the file side recorded the admin as the uploader, so a private photo was
neither viewable nor replaceable by the member it belongs to. Naming the
member in ``ownerUsername`` makes them the uploader of record.
"""

import json

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.user import User
from club_server.utils import now_utc_ms

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    png_bytes,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

PRIVATE_ROLES = ["self", "admin", "coach"]


async def upload_for(
    client: AsyncClient,
    token: str,
    owner: str | None,
    access_roles: list[str] | None = None,
) -> Response:
    """POST one image, naming ``owner`` in ``ownerUsername`` when given."""
    data = {"preserveOriginal": "true"}
    if owner is not None:
        data["ownerUsername"] = owner
    if access_roles is not None:
        data["accessRoles"] = json.dumps(access_roles)
    return await client.post(
        "/v1/media",
        files={"file": ("photo.png", png_bytes(), "image/png")},
        data=data,
        headers=auth(token),
    )


async def total_media(client: AsyncClient, admin: str) -> int:
    listing = await client.get("/v1/media", headers=auth(admin))
    assert listing.status_code == 200, listing.text
    return listing.json()["total"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_record_the_named_user_as_uploader_when_an_admin_uploads_for_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    alice = await create_member_user(db_session, "alice")

    response = await upload_for(client, admin, "alice", PRIVATE_ROLES)
    assert response.status_code == 201, response.text
    assert response.json()["uploadedBy"] == "alice"

    as_owner = await client.get(
        f"/v1/media/by_id/{response.json()['id']}", headers=auth(alice)
    )
    assert as_owner.status_code == 200, as_owner.text
    assert as_owner.json()["uploadedBy"] == "alice"
    assert as_owner.json()["accessRoles"] == PRIVATE_ROLES


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_record_the_named_user_as_uploader_when_the_super_admin_uploads_for_them(
    client: AsyncClient, db_session: AsyncSession
):
    sudo = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")

    response = await upload_for(client, sudo, "alice", PRIVATE_ROLES)
    assert response.status_code == 201, response.text

    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert mine.status_code == 200
    assert [m["id"] for m in mine.json()["items"]] == [response.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_let_the_named_user_download_a_private_file_uploaded_for_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    record = (await upload_for(client, admin, "alice", PRIVATE_ROLES)).json()
    url = f"/v1/media/by_id/{record['uuid']}/download"

    as_owner = await client.get(url, headers=auth(alice))
    assert as_owner.status_code == 200, as_owner.text
    assert as_owner.content == png_bytes()

    as_other_member = await client.get(url, headers=auth(bob))
    assert as_other_member.status_code == 403, as_other_member.text


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_let_the_named_user_change_access_roles_of_a_file_uploaded_for_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    alice = await create_member_user(db_session, "alice")
    record = (await upload_for(client, admin, "alice", PRIVATE_ROLES)).json()

    changed = await client.patch(
        f"/v1/media/by_id/{record['id']}",
        json={"accessRoles": ["public"]},
        headers=auth(alice),
    )
    assert changed.status_code == 200, changed.text

    read_back = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(admin))
    assert read_back.status_code == 200
    assert read_back.json()["accessRoles"] == ["public"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_let_the_named_user_soft_delete_a_file_uploaded_for_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    alice = await create_member_user(db_session, "alice")
    record = (await upload_for(client, admin, "alice", PRIVATE_ROLES)).json()

    deleted = await client.delete(
        f"/v1/media/by_id/{record['id']}", headers=auth(alice)
    )
    assert deleted.status_code == 204, deleted.text

    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert mine.json()["total"] == 0
    as_admin = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(admin))
    assert as_admin.status_code == 200
    assert as_admin.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_list_the_file_under_the_named_user_and_not_the_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    alice = await create_member_user(db_session, "alice")
    record = (await upload_for(client, admin, "alice", PRIVATE_ROLES)).json()

    owners = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert owners.status_code == 200
    assert [m["id"] for m in owners.json()["items"]] == [record["id"]]

    admins = await client.get("/v1/media/myfiles", headers=auth(admin))
    assert admins.status_code == 200
    assert admins.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_record_the_caller_as_uploader_when_no_owner_is_named(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")

    response = await upload_for(client, admin, None)
    assert response.status_code == 201, response.text
    assert response.json()["uploadedBy"] == "boss"

    mine = await client.get("/v1/media/myfiles", headers=auth(admin))
    assert mine.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6b")
async def test_should_refuse_a_coach_uploading_on_behalf_of_someone_else(
    client: AsyncClient, db_session: AsyncSession
):
    sudo = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "carl")
    alice = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await upload_for(client, coach, "alice", PRIVATE_ROLES)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "FORBIDDEN"

    assert await total_media(client, sudo) == 0
    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert mine.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6b")
async def test_should_refuse_a_member_uploading_on_behalf_of_someone_else(
    client: AsyncClient, db_session: AsyncSession
):
    sudo = await create_admin_user(db_session)
    bob = await create_member_user(db_session, "bob")
    alice = await create_member_user(db_session, "alice")
    await db_session.commit()

    response = await upload_for(client, bob, "alice", PRIVATE_ROLES)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "FORBIDDEN"

    assert await total_media(client, sudo) == 0
    mine = await client.get("/v1/media/myfiles", headers=auth(alice))
    assert mine.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6b")
async def test_should_refuse_a_member_naming_an_unknown_user_without_revealing_it(
    client: AsyncClient, db_session: AsyncSession
):
    """403 before the lookup, so the field cannot be used to probe usernames."""
    bob = await create_member_user(db_session, "bob")
    await db_session.commit()

    response = await upload_for(client, bob, "nobody")
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6b")
@pytest.mark.parametrize("role", ["member", "coach"])
async def test_should_accept_a_non_admin_naming_themselves_as_owner(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    create = create_member_user if role == "member" else create_coach_user
    token = await create(db_session, "alice")

    response = await upload_for(client, token, "alice", PRIVATE_ROLES)
    assert response.status_code == 201, response.text
    assert response.json()["uploadedBy"] == "alice"

    mine = await client.get("/v1/media/myfiles", headers=auth(token))
    assert [m["id"] for m in mine.json()["items"]] == [response.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6c")
async def test_should_refuse_an_upload_on_behalf_of_an_unknown_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    await db_session.commit()

    response = await upload_for(client, admin, "nobody", PRIVATE_ROLES)
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    assert await total_media(client, admin) == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6c")
async def test_should_refuse_an_upload_on_behalf_of_a_deleted_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    _ = await create_member_user(db_session, "alice")
    _ = await db_session.execute(
        update(User).where(User.username == "alice").values(deleted_at=now_utc_ms())
    )
    await db_session.commit()

    response = await upload_for(client, admin, "alice", PRIVATE_ROLES)
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    assert await total_media(client, admin) == 0


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6d")
async def test_should_audit_the_admin_as_actor_and_the_named_user_as_owner(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    _ = await create_member_user(db_session, "alice")
    record = (await upload_for(client, admin, "alice", PRIVATE_ROLES)).json()

    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "media",
                AuditLog.resource_id == str(record["id"]),
            )
        )
    ).scalar_one()
    assert row.action == "upload_media_v2"
    assert row.actor_username == "boss"
    assert row.target_username == "alice"
    assert json.loads(row.details or "{}")["owner_username"] == "alice"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6d")
async def test_should_audit_no_owner_when_the_caller_uploads_for_themselves(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    record = (await upload_for(client, admin, None)).json()

    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "media",
                AuditLog.resource_id == str(record["id"]),
            )
        )
    ).scalar_one()
    assert row.actor_username == "boss"
    assert row.target_username is None
    assert "owner_username" not in json.loads(row.details or "{}")


@pytest.mark.asyncio
@pytest.mark.requirement("media:R6a")
async def test_should_describe_owner_username_in_openapi(client: AsyncClient):
    schema = (await client.get("/openapi.json")).json()

    body = schema["paths"]["/v1/media"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    if "$ref" in body:
        body = schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
    field = body["properties"]["ownerUsername"]

    assert "on behalf" in field["description"]
    assert "ownerUsername" not in body.get("required", [])
