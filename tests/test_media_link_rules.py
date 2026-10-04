"""Link rules of media_requirements.md (#494)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
)
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    pdf_bytes,
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")


async def _owners(client: AsyncClient, admin: str) -> dict[str, int]:
    """One venue, one group and one future event. Returns their ids by type."""
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(admin))
    assert venue.status_code == 201, venue.text
    group = await client.post("/v1/groups", json={"name": "U12"}, headers=auth(admin))
    assert group.status_code == 201, group.text
    start = datetime.now(timezone.utc) + timedelta(hours=24)
    event = await client.post(
        "/v1/events",
        json={
            "title": "Skills",
            "type": "oneOff",
            "visibility": "public",
            "venueId": venue.json()["id"],
            "startTimeUtc": int(start.timestamp() * 1000),
            "endTimeUtc": int((start + timedelta(hours=2)).timestamp() * 1000),
        },
        headers=auth(admin),
    )
    assert event.status_code == 201, event.text
    return {
        "venues": venue.json()["id"],
        "groups": group.json()["id"],
        "events": event.json()["id"],
    }


@pytest.mark.asyncio
@pytest.mark.requirement("media:R56")
@pytest.mark.parametrize(
    "body",
    [
        {"tag": "bad tag!"},
        {"tag": "t" * 65},
        {"tag": ""},
        {"tag": "ok", "metadata": "m" * 2049},
        {"tag": "ok", "extra": 1},
    ],
)
async def test_should_refuse_link_when_tag_metadata_or_fields_are_invalid(
    client: AsyncClient, db_session: AsyncSession, body: dict
):
    admin = await create_admin_user(db_session)
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(admin))
    image = await upload(client, admin)
    await db_session.commit()

    response = await client.post(
        f"/v1/venues/by_id/{venue.json()['id']}/media",
        json={**body, "mediaUuid": image["uuid"]},
        headers=auth(admin),
    )
    assert response.status_code == 422, response.text

    listing = await client.get(
        f"/v1/venues/by_id/{venue.json()['id']}/media", headers=auth(admin)
    )
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R56")
async def test_should_accept_link_when_tag_and_metadata_are_at_their_limits(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(admin))
    image = await upload(client, admin)
    tag = "A-z_9" + "x" * 59

    response = await client.post(
        f"/v1/venues/by_id/{venue.json()['id']}/media",
        json={"tag": tag, "mediaUuid": image["uuid"], "metadata": "m" * 2048},
        headers=auth(admin),
    )
    assert response.status_code == 201, response.text

    listing = await client.get(
        f"/v1/venues/by_id/{venue.json()['id']}/media/{tag}", headers=auth(admin)
    )
    assert len(listing.json()) == 1
    assert len(listing.json()[0]["metadata"]) == 2048


@pytest.mark.asyncio
@pytest.mark.requirement("media:R59")
async def test_should_refuse_link_when_media_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    image = await upload(client, alice)
    deleted = await client.delete(f"/v1/media/by_id/{image['id']}", headers=auth(alice))
    assert deleted.status_code == 204
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": image["uuid"]},
        headers=auth(alice),
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "MEDIA_NOT_FOUND"

    listing = await client.get("/v1/users/by_id/alice/media", headers=auth(alice))
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
async def test_should_list_links_when_event_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    owners = await _owners(client, admin)
    image = await upload(client, admin)
    linked = await client.post(
        f"/v1/events/by_id/{owners['events']}/media",
        json={"tag": "event_cover", "mediaUuid": image["uuid"]},
        headers=auth(admin),
    )
    assert linked.status_code == 201
    deleted = await client.delete(
        f"/v1/events/by_id/{owners['events']}", headers=auth(admin)
    )
    assert deleted.status_code == 200, deleted.text
    await db_session.commit()

    response = await client.get(
        f"/v1/events/by_id/{owners['events']}/media", headers=auth(admin)
    )
    assert response.status_code == 200, response.text
    assert [row["media"]["uuid"] for row in response.json()["event_cover"]] == [
        image["uuid"]
    ]
    assert response.json()["event_cover"][0]["ownerDeleted"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", ["groups", "venues"])
async def test_should_keep_links_read_only_when_group_or_venue_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    owners = await _owners(client, admin)
    if owner == "venues":  # a venue in use by an event cannot be soft-deleted
        venue = await client.post(
            "/v1/venues", json={"name": "Spare"}, headers=auth(admin)
        )
        owners["venues"] = venue.json()["id"]
    first, second = await upload(client, admin), await upload(client, admin)
    base = f"/v1/{owner}/by_id/{owners[owner]}/media"
    linked = await client.post(
        base, json={"tag": "logo", "mediaUuid": first["uuid"]}, headers=auth(admin)
    )
    assert linked.status_code == 201
    deleted = await client.delete(
        f"/v1/{owner}/by_id/{owners[owner]}", headers=auth(admin)
    )
    assert deleted.status_code == 200, deleted.text

    added = await client.post(
        base, json={"tag": "logo", "mediaUuid": second["uuid"]}, headers=auth(admin)
    )
    assert added.status_code == 422, added.text
    assert added.json()["detail"]["code"] == "OWNER_DELETED"
    listing = await client.get(f"{base}/logo", headers=auth(admin))
    assert listing.status_code == 200
    assert [row["media"]["uuid"] for row in listing.json()] == [first["uuid"]]
    assert listing.json()[0]["ownerDeleted"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("media:R65")
async def test_should_answer_not_found_when_changing_or_removing_missing_link(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(admin))
    image = await upload(client, admin)
    base = f"/v1/venues/by_id/{venue.json()['id']}/media"
    await db_session.commit()

    patched = await client.patch(
        f"{base}/banner/{image['uuid']}", json={"metadata": "x"}, headers=auth(admin)
    )
    assert patched.status_code == 404
    assert patched.json()["detail"]["code"] == "MEDIA_LINK_NOT_FOUND"

    removed = await client.delete(f"{base}/banner/{image['uuid']}", headers=auth(admin))
    assert removed.status_code == 404
    assert removed.json()["detail"]["code"] == "MEDIA_LINK_NOT_FOUND"

    listing = await client.get(base, headers=auth(admin))
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R72")
async def test_should_forbid_member_reading_another_users_links(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    avatar = await upload(client, alice)
    linked = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": avatar["uuid"]},
        headers=auth(alice),
    )
    assert linked.status_code == 201
    await db_session.commit()

    response = await client.get("/v1/users/by_id/alice/media", headers=auth(bob))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"

    own = await client.get("/v1/users/by_id/alice/media", headers=auth(alice))
    assert list(own.json()) == ["avatar"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R72")
async def test_should_forbid_coach_writing_another_users_links(
    client: AsyncClient, db_session: AsyncSession
):
    coach = await create_coach_user(db_session, "cora")
    _ = await create_member_user(db_session, "alice")
    image = await upload(client, coach)
    await db_session.commit()

    response = await client.post(
        "/v1/users/by_id/alice/media",
        json={"tag": "avatar", "mediaUuid": image["uuid"]},
        headers=auth(coach),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"

    listing = await client.get("/v1/users/by_id/alice/media", headers=auth(coach))
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R73")
@pytest.mark.requirement("media:R74")
@pytest.mark.parametrize("owner", ["events", "groups", "venues"])
async def test_should_let_coach_write_and_member_read_owner_links(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    member = await create_member_user(db_session, "alice")
    owners = await _owners(client, admin)
    image = await upload(client, coach)
    base = f"/v1/{owner}/by_id/{owners[owner]}/media"

    written = await client.post(
        base, json={"tag": "gallery", "mediaUuid": image["uuid"]}, headers=auth(coach)
    )
    assert written.status_code == 201, written.text

    read = await client.get(base, headers=auth(member))
    assert read.status_code == 200, read.text
    assert [row["media"]["uuid"] for row in read.json()["gallery"]] == [image["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R76")
@pytest.mark.parametrize("owner", ["users", "events", "groups", "venues"])
async def test_should_refuse_anonymous_caller_on_link_reads_and_writes(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    owners = await _owners(client, admin)
    owners["users"] = "admin"
    image = await upload(client, admin)
    base = f"/v1/{owner}/by_id/{owners[owner]}/media"
    await db_session.commit()

    for method, url, body in [
        ("GET", base, None),
        ("GET", f"{base}/tag", None),
        ("POST", base, {"tag": "tag", "mediaUuid": image["uuid"]}),
        ("DELETE", f"{base}/tag", None),
    ]:
        response = await client.request(method, url, json=body)
        assert response.status_code == 401, (method, url, response.text)

    listing = await client.get(base, headers=auth(admin))
    assert listing.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R81")
async def test_should_search_newest_first_and_narrow_by_media_type(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await client.post("/v1/venues", json={"name": "Rink"}, headers=auth(admin))
    base = f"/v1/venues/by_id/{venue.json()['id']}/media"
    image = await upload(client, admin)
    pdf = await upload(
        client,
        admin,
        filename="map.pdf",
        content=pdf_bytes(),
        content_type="application/pdf",
    )
    for item in (image, pdf):
        linked = await client.post(
            base, json={"tag": "info", "mediaUuid": item["uuid"]}, headers=auth(admin)
        )
        assert linked.status_code == 201
        await asyncio.sleep(0.01)

    everything = await client.get("/v1/media/links", headers=auth(admin))
    assert [row["mediaUuid"] for row in everything.json()["items"]] == [
        pdf["uuid"],
        image["uuid"],
    ]

    pdfs = await client.get("/v1/media/links?mediaType=pdf", headers=auth(admin))
    assert pdfs.json()["total"] == 1
    assert [row["mediaUuid"] for row in pdfs.json()["items"]] == [pdf["uuid"]]
    assert pdfs.json()["items"][0]["mediaType"] == "pdf"
