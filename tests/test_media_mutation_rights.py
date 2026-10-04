"""Who may change, soft-delete and restore a media item (#503).

Only the uploader, an admin and the super admin, whatever the item's
access roles (media:R43). Anyone else is refused: with 403 when they can
view the item (media:R44, R46), with 404 when they cannot (media:R45).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
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


async def _record(client: AsyncClient, token: str, media_id: int) -> dict:
    response = await client.get(f"/v1/media/by_id/{media_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _assert_forbidden(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


def _assert_not_found(response) -> None:
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_should_let_admin_change_roles_when_roles_admit_only_the_uploader(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    boss = await create_regular_admin_user(db_session, "boss")
    item = await upload(client, alice, access_roles=["self"])

    changed = await client.patch(
        f"/v1/media/by_id/{item['id']}",
        json={"accessRoles": ["self", "admin"]},
        headers=auth(boss),
    )

    assert changed.status_code == 200, changed.text
    assert (await _record(client, alice, item["id"]))["accessRoles"] == [
        "self",
        "admin",
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R43")
async def test_should_let_admin_delete_and_restore_when_roles_admit_only_the_uploader(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    boss = await create_regular_admin_user(db_session, "boss")
    item = await upload(client, alice, access_roles=["self"])

    deleted = await client.delete(f"/v1/media/by_id/{item['id']}", headers=auth(boss))
    assert deleted.status_code == 204, deleted.text
    assert (await _record(client, alice, item["id"]))["deletedAtUtc"] is not None

    restored = await client.post(
        f"/v1/media/by_id/{item['id']}/restore", headers=auth(boss)
    )
    assert restored.status_code == 200, restored.text
    assert (await _record(client, alice, item["id"]))["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R44")
@pytest.mark.parametrize("roles", [["public"], ["coach"]])
async def test_should_refuse_coach_changing_roles_when_coach_can_view_media(
    client: AsyncClient, db_session: AsyncSession, roles: list[str]
):
    alice = await create_member_user(db_session, "alice")
    coach = await create_coach_user(db_session, "carl")
    item = await upload(client, alice, access_roles=roles)
    await db_session.commit()

    changed = await client.patch(
        f"/v1/media/by_id/{item['id']}",
        json={"accessRoles": ["self"]},
        headers=auth(coach),
    )

    _assert_forbidden(changed)
    assert (await _record(client, alice, item["id"]))["accessRoles"] == roles


@pytest.mark.asyncio
@pytest.mark.requirement("media:R44")
@pytest.mark.parametrize("roles", [["public"], ["coach"]])
async def test_should_refuse_coach_soft_deleting_when_coach_can_view_media(
    client: AsyncClient, db_session: AsyncSession, roles: list[str]
):
    alice = await create_member_user(db_session, "alice")
    coach = await create_coach_user(db_session, "carl")
    item = await upload(client, alice, access_roles=roles)
    await db_session.commit()

    deleted = await client.delete(f"/v1/media/by_id/{item['id']}", headers=auth(coach))

    _assert_forbidden(deleted)
    assert (await _record(client, alice, item["id"]))["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R44")
async def test_should_refuse_coach_restoring_when_coach_can_view_media(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    coach = await create_coach_user(db_session, "carl")
    item = await upload(client, alice, access_roles=["public"])
    gone = await client.delete(f"/v1/media/by_id/{item['id']}", headers=auth(alice))
    assert gone.status_code == 204, gone.text
    await db_session.commit()

    restored = await client.post(
        f"/v1/media/by_id/{item['id']}/restore", headers=auth(coach)
    )

    _assert_forbidden(restored)
    assert (await _record(client, alice, item["id"]))["deletedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R46")
async def test_should_refuse_member_restoring_when_public_media_is_not_theirs(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    item = await upload(client, alice)
    gone = await client.delete(f"/v1/media/by_id/{item['id']}", headers=auth(alice))
    assert gone.status_code == 204, gone.text
    await db_session.commit()

    restored = await client.post(
        f"/v1/media/by_id/{item['id']}/restore", headers=auth(bob)
    )

    _assert_forbidden(restored)
    assert (await _record(client, alice, item["id"]))["deletedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R45")
async def test_should_answer_not_found_when_member_cannot_view_media(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    item = await upload(client, alice, access_roles=["self"])
    await db_session.commit()

    changed = await client.patch(
        f"/v1/media/by_id/{item['id']}",
        json={"accessRoles": ["public"]},
        headers=auth(bob),
    )
    deleted = await client.delete(f"/v1/media/by_id/{item['id']}", headers=auth(bob))

    _assert_not_found(changed)
    _assert_not_found(deleted)
    record = await _record(client, alice, item["id"])
    assert record["accessRoles"] == ["self"]
    assert record["deletedAtUtc"] is None
