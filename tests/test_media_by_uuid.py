"""A media record is read by its uuid (#27, media R40a–R40b).

Links carry a media item's uuid; changing or deleting the item takes its id.
Reading the record by uuid is the step between the two, for the same callers
and with the same answer as reading it by id.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40a")
async def test_should_return_the_record_when_the_uploader_reads_it_by_uuid(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, access_roles=["self"])

    by_uuid = await client.get(
        f"/v1/media/by_uuid/{record['uuid']}", headers=auth(alice)
    )
    assert by_uuid.status_code == 200, by_uuid.text
    assert by_uuid.json()["id"] == record["id"]

    by_id = await client.get(f"/v1/media/by_id/{record['id']}", headers=auth(alice))
    assert by_id.status_code == 200, by_id.text
    assert by_uuid.json() == by_id.json()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40a")
async def test_should_return_the_record_when_an_admin_reads_a_private_file_by_uuid(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, access_roles=["self"])

    read = await client.get(f"/v1/media/by_uuid/{record['uuid']}", headers=auth(admin))
    assert read.status_code == 200, read.text
    assert read.json()["id"] == record["id"]
    assert read.json()["uploadedBy"] == "alice"
    assert read.json()["accessRoles"] == ["self"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40a")
async def test_should_return_the_record_when_a_coach_reads_a_private_file_by_uuid(
    client: AsyncClient, db_session: AsyncSession
):
    coach = await create_coach_user(db_session, "cora")
    alice = await create_member_user(db_session, "alice")
    record = await upload(client, alice, access_roles=["self"])

    read = await client.get(f"/v1/media/by_uuid/{record['uuid']}", headers=auth(coach))
    assert read.status_code == 200, read.text
    assert read.json()["id"] == record["id"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40a")
async def test_should_return_the_record_by_uuid_when_the_media_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    deleted = await client.delete(
        f"/v1/media/by_id/{record['id']}", headers=auth(admin)
    )
    assert deleted.status_code == 204, deleted.text

    read = await client.get(f"/v1/media/by_uuid/{record['uuid']}", headers=auth(admin))
    assert read.status_code == 200, read.text
    assert read.json()["id"] == record["id"]
    assert read.json()["deletedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40b")
async def test_should_answer_not_found_when_another_member_reads_a_file_by_uuid(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    public = await upload(client, alice, access_roles=["public"])

    read = await client.get(f"/v1/media/by_uuid/{public['uuid']}", headers=auth(bob))
    assert read.status_code == 404, read.text
    assert read.json()["detail"]["code"] == "MEDIA_NOT_FOUND"

    unknown = await client.get(f"/v1/media/by_uuid/{UNKNOWN_UUID}", headers=auth(bob))
    assert unknown.status_code == 404, unknown.text
    assert unknown.json() == read.json()


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40b")
async def test_should_answer_not_found_when_no_media_has_the_uuid(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")

    unknown = await client.get(f"/v1/media/by_uuid/{UNKNOWN_UUID}", headers=auth(admin))
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40b")
async def test_should_answer_not_found_when_the_uuid_is_malformed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session, "boss")

    malformed = await client.get("/v1/media/by_uuid/not-a-uuid", headers=auth(admin))
    assert malformed.status_code == 404, malformed.text
    assert malformed.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40b")
async def test_should_refuse_an_anonymous_caller_reading_a_file_by_uuid(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    public = await upload(client, alice, access_roles=["public"])

    read = await client.get(f"/v1/media/by_uuid/{public['uuid']}")
    assert read.status_code == 401, read.text


@pytest.mark.asyncio
@pytest.mark.requirement("media:R40a")
async def test_should_describe_the_read_by_uuid_in_openapi(client: AsyncClient):
    schema = (await client.get("/openapi.json")).json()

    operation = schema["paths"]["/v1/media/by_uuid/{uuid}"]["get"]

    assert "uuid" in operation["summary"].lower()
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/MediaResponse")
