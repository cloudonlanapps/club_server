"""Media links on a soft-deleted group, venue or event (#517, media:R61).

The links stay listed, each marked `ownerDeleted`, on the owner's own
listings, on the reverse lookup of a media item and on the cross-owner
search. They are read-only while the owner is deleted: adding, changing or
removing one → 422 `OWNER_DELETED`.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)
from .redesign_helpers import create_oneoff, create_venue

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

OWNERS = ["events", "groups", "venues"]
OWNER_TYPE = {"events": "event", "groups": "group", "venues": "venue"}
TAG = "gallery"


async def _owner(client: AsyncClient, admin: str, owner: str) -> int:
    """A live owner of the given kind, free to be soft-deleted."""
    if owner == "venues":
        return await create_venue(client, admin, name="Spare rink")
    if owner == "groups":
        response = await client.post(
            "/v1/groups", json={"name": "U12"}, headers=auth(admin)
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]
    event = await create_oneoff(client, admin, await create_venue(client, admin))
    return event["id"]


async def _linked_then_deleted(
    client: AsyncClient, db_session: AsyncSession, admin: str, owner: str
) -> tuple[str, dict]:
    """Link one image to a fresh owner, soft-delete the owner.

    Returns the owner's link base url and the linked media record.
    """
    owner_id = await _owner(client, admin, owner)
    image = await upload(client, admin)
    base = f"/v1/{owner}/by_id/{owner_id}/media"
    linked = await client.post(
        base,
        json={"tag": TAG, "mediaUuid": image["uuid"], "metadata": "first"},
        headers=auth(admin),
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["ownerDeleted"] is False
    deleted = await client.delete(f"/v1/{owner}/by_id/{owner_id}", headers=auth(admin))
    assert deleted.status_code == 200, deleted.text
    await db_session.commit()
    return base, image


async def _assert_links_unchanged(
    client: AsyncClient, token: str, base: str, image: dict
) -> None:
    listing = await client.get(f"{base}/{TAG}", headers=auth(token))
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert [row["media"]["uuid"] for row in rows] == [image["uuid"]]
    assert rows[0]["metadata"] == "first"
    assert rows[0]["ownerDeleted"] is True


def _assert_owner_deleted(response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "OWNER_DELETED"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_list_links_marked_owner_deleted_when_owner_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mia")
    base, image = await _linked_then_deleted(client, db_session, admin, owner)

    for token in (admin, member):
        grouped = await client.get(base, headers=auth(token))
        assert grouped.status_code == 200, grouped.text
        assert [row["media"]["uuid"] for row in grouped.json()[TAG]] == [image["uuid"]]
        assert grouped.json()[TAG][0]["ownerDeleted"] is True
        one = await client.get(f"{base}/{TAG}/{image['uuid']}", headers=auth(token))
        assert one.status_code == 200, one.text
        assert one.json()["ownerDeleted"] is True
    await _assert_links_unchanged(client, member, base, image)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_mark_links_owner_live_when_owner_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    owner_id = await _owner(client, admin, owner)
    image = await upload(client, admin)
    base = f"/v1/{owner}/by_id/{owner_id}/media"
    linked = await client.post(
        base, json={"tag": TAG, "mediaUuid": image["uuid"]}, headers=auth(admin)
    )
    assert linked.status_code == 201, linked.text

    listing = await client.get(f"{base}/{TAG}", headers=auth(admin))

    assert listing.status_code == 200, listing.text
    assert [row["ownerDeleted"] for row in listing.json()] == [False]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_refuse_adding_link_when_owner_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "carl")
    base, image = await _linked_then_deleted(client, db_session, admin, owner)
    second = await upload(client, admin)
    await db_session.commit()

    for token in (admin, coach):
        added = await client.post(
            base, json={"tag": TAG, "mediaUuid": second["uuid"]}, headers=auth(token)
        )
        _assert_owner_deleted(added)
    await _assert_links_unchanged(client, admin, base, image)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_refuse_changing_link_when_owner_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    base, image = await _linked_then_deleted(client, db_session, admin, owner)

    changed = await client.patch(
        f"{base}/{TAG}/{image['uuid']}",
        json={"metadata": "second"},
        headers=auth(admin),
    )

    _assert_owner_deleted(changed)
    await _assert_links_unchanged(client, admin, base, image)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_refuse_removing_links_when_owner_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    base, image = await _linked_then_deleted(client, db_session, admin, owner)

    removed_one = await client.delete(
        f"{base}/{TAG}/{image['uuid']}", headers=auth(admin)
    )
    removed_tag = await client.delete(f"{base}/{TAG}", headers=auth(admin))

    _assert_owner_deleted(removed_one)
    _assert_owner_deleted(removed_tag)
    await _assert_links_unchanged(client, admin, base, image)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_mark_owner_deleted_on_reverse_lookup_when_owner_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mia")
    _, image = await _linked_then_deleted(client, db_session, admin, owner)
    live_group = await _owner(client, admin, "groups")
    also = await client.post(
        f"/v1/groups/by_id/{live_group}/media",
        json={"tag": TAG, "mediaUuid": image["uuid"]},
        headers=auth(admin),
    )
    assert also.status_code == 201, also.text

    for token in (admin, member):
        lookup = await client.get(
            f"/v1/media/by_id/{image['uuid']}/links", headers=auth(token)
        )
        assert lookup.status_code == 200, lookup.text
        marks = {(row["ownerType"], row["ownerDeleted"]) for row in lookup.json()}
        assert (OWNER_TYPE[owner], True) in marks
        assert ("group", False) in marks
        assert len(lookup.json()) == 2


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_mark_owner_deleted_on_link_search_when_owner_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "carl")
    _, image = await _linked_then_deleted(client, db_session, admin, owner)

    for token in (admin, coach):
        search = await client.get(
            "/v1/media/links",
            params={"ownerType": OWNER_TYPE[owner]},
            headers=auth(token),
        )
        assert search.status_code == 200, search.text
        items = search.json()["items"]
        assert [(row["mediaUuid"], row["ownerDeleted"]) for row in items] == [
            (image["uuid"], True)
        ]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R61")
@pytest.mark.parametrize("owner", OWNERS)
async def test_should_accept_link_writes_again_when_owner_is_restored(
    client: AsyncClient, db_session: AsyncSession, owner: str
):
    admin = await create_admin_user(db_session)
    base, image = await _linked_then_deleted(client, db_session, admin, owner)
    owner_url = base.removesuffix("/media")
    restored = await client.post(f"{owner_url}/restore", headers=auth(admin))
    assert restored.status_code == 200, restored.text

    changed = await client.patch(
        f"{base}/{TAG}/{image['uuid']}",
        json={"metadata": "second"},
        headers=auth(admin),
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["ownerDeleted"] is False
    listing = await client.get(f"{base}/{TAG}", headers=auth(admin))
    assert [(r["metadata"], r["ownerDeleted"]) for r in listing.json()] == [
        ("second", False)
    ]
