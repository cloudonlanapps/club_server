"""A user has one avatar: cases the first tests left out (#32, #33).

A replaced file that something else links (#32, media R69b): the old avatar
file is also linked from an event, a group, a venue or an evaluation.
Replacing the avatar removes the user's link only; the file and the other
owner's link stay.

Replacing is one transaction (#33, media R69a): a failure after the new link
is written leaves the old link and file as they were and the new link absent.

The rule itself is club_server#28.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from club_server.constants import USER_AVATAR_TAG
from club_server.db.models.media import Media
from club_server.db.models.media_links import UserMediaLink
from club_server.routers import media_links as media_links_router
from club_server.services import media_links as media_links_service
from club_server.services.auth import AuthService

from .evaluation_helpers import (
    create_general_evaluation,
    create_template,
    item_ids,
    rating_item,
)
from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)
from .redesign_helpers import create_oneoff, create_venue
from .test_user_avatar_single import PRIVATE_ROLES, deleted_at, link, linked_uuids

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

ALICE = "users/by_id/alice"
OTHER_TAG = "gallery"


async def _avatar_also_linked_from(
    client: AsyncClient, alice: str, writer: str, owner_path: str, tag: str
) -> dict:
    """Alice's avatar, its file linked by ``writer`` to a second owner too."""
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, ALICE, old["uuid"])
    await link(client, writer, owner_path, old["uuid"], tag=tag)
    assert await linked_uuids(client, alice, ALICE) == [old["uuid"]]
    assert await linked_uuids(client, writer, owner_path, tag) == [old["uuid"]]
    return old


async def _replace_and_assert_file_kept(
    client: AsyncClient, alice: str, reader: str, owner_path: str, tag: str, old: dict
) -> None:
    """Link a new avatar; the old file and its other link are still there."""
    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, ALICE, new["uuid"])

    assert await linked_uuids(client, alice, ALICE) == [new["uuid"]]
    assert await linked_uuids(client, reader, ALICE) == [new["uuid"]]
    assert await linked_uuids(client, reader, owner_path, tag) == [old["uuid"]]
    assert await deleted_at(client, alice, old["id"]) is None
    assert await deleted_at(client, alice, new["id"]) is None


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69b")
async def test_should_keep_a_replaced_avatar_file_when_an_event_links_it(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    sudo = await create_admin_user(db_session)
    event = await create_oneoff(client, sudo, await create_venue(client, sudo))
    owner = f"events/by_id/{event['id']}"
    old = await _avatar_also_linked_from(client, alice, sudo, owner, OTHER_TAG)

    await _replace_and_assert_file_kept(client, alice, sudo, owner, OTHER_TAG, old)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69b")
async def test_should_keep_a_replaced_avatar_file_when_a_group_links_it(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    sudo = await create_admin_user(db_session)
    group = await client.post("/v1/groups", json={"name": "U12"}, headers=auth(sudo))
    assert group.status_code == 201, group.text
    owner = f"groups/by_id/{group.json()['id']}"
    old = await _avatar_also_linked_from(client, alice, sudo, owner, OTHER_TAG)

    await _replace_and_assert_file_kept(client, alice, sudo, owner, OTHER_TAG, old)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69b")
async def test_should_keep_a_replaced_avatar_file_when_a_venue_links_it(
    client: AsyncClient, db_session: AsyncSession
):
    alice = await create_member_user(db_session, "alice")
    sudo = await create_admin_user(db_session)
    owner = f"venues/by_id/{await create_venue(client, sudo)}"
    old = await _avatar_also_linked_from(client, alice, sudo, owner, OTHER_TAG)

    await _replace_and_assert_file_kept(client, alice, sudo, owner, OTHER_TAG, old)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69b")
async def test_should_keep_a_replaced_avatar_file_when_an_evaluation_links_it(
    client: AsyncClient,
    db_session: AsyncSession,
    evaluations_enabled: None,
):
    """The file is evidence on a draft evaluation its coach wrote about alice."""
    alice = await create_member_user(db_session, "alice")
    sudo = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    template_id = await create_template(client, sudo, layout=[rating_item("Skating")])
    question_tag = str((await item_ids(client, sudo, template_id))[0])
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    owner = f"evaluations/by_id/{evaluation_id}"
    old = await _avatar_also_linked_from(client, alice, coach, owner, question_tag)

    await _replace_and_assert_file_kept(client, alice, coach, owner, question_tag, old)


# ---------- replacing an avatar is one transaction (#33) ----------


async def _avatar_state(db: AsyncSession) -> tuple[list[str], list[str]]:
    """Alice's avatar uuids, and the uuids of the soft-deleted files, as ``db`` sees them."""
    linked = await db.execute(
        select(UserMediaLink.media_uuid)
        .where(UserMediaLink.username == "alice", UserMediaLink.tag == USER_AVATAR_TAG)
        .order_by(UserMediaLink.created_at)
    )
    gone = await db.execute(select(Media.uuid).where(Media.deleted_at.is_not(None)))
    return list(linked.scalars().all()), list(gone.scalars().all())


async def _assert_old_avatar_untouched(
    client: AsyncClient, test_engine: AsyncEngine, alice: str, old: dict, new: dict
) -> None:
    """The old link and file are as they were and the new link is absent:
    through the API as the member and the super admin, and in a session of
    its own, which sees only what was committed."""
    sudo = AuthService.create_access_token("admin", is_super_admin=True)
    assert await linked_uuids(client, alice, ALICE) == [old["uuid"]]
    assert await linked_uuids(client, sudo, ALICE) == [old["uuid"]]
    assert await deleted_at(client, alice, old["id"]) is None
    assert await deleted_at(client, alice, new["id"]) is None
    async with AsyncSession(test_engine) as fresh:
        assert await _avatar_state(fresh) == ([old["uuid"]], [])


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69a")
async def test_should_keep_the_old_avatar_when_deleting_the_old_file_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    alice = await create_member_user(db_session, "alice")
    _ = await create_admin_user(db_session)
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, ALICE, old["uuid"])
    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    seen: list[tuple[list[str], list[str]]] = []

    async def fail_once_the_new_link_is_written(db: AsyncSession, media_uuid: str):
        seen.append(await _avatar_state(db))
        raise RuntimeError("storage went away")

    monkeypatch.setattr(
        media_links_service, "check_media_in_use", fail_once_the_new_link_is_written
    )
    with pytest.raises(RuntimeError, match="storage went away"):
        await link(client, alice, ALICE, new["uuid"])

    # The step that failed ran with the new link written and the old one removed.
    assert seen == [([new["uuid"]], [])]
    await _assert_old_avatar_untouched(client, test_engine, alice, old, new)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R69a")
async def test_should_keep_the_old_avatar_when_auditing_the_new_link_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    alice = await create_member_user(db_session, "alice")
    _ = await create_admin_user(db_session)
    old = await upload(client, alice, access_roles=PRIVATE_ROLES)
    await link(client, alice, ALICE, old["uuid"])
    new = await upload(client, alice, access_roles=PRIVATE_ROLES)
    seen: list[tuple[list[str], list[str]]] = []

    async def fail_once_the_old_file_is_deleted(db: AsyncSession, *args, **kwargs):
        seen.append(await _avatar_state(db))
        raise RuntimeError("audit log went away")

    monkeypatch.setattr(
        media_links_router, "_audit_create", fail_once_the_old_file_is_deleted
    )
    with pytest.raises(RuntimeError, match="audit log went away"):
        await link(client, alice, ALICE, new["uuid"])

    # The step that failed ran with the whole replace written, old file deleted.
    assert seen == [([new["uuid"]], [old["uuid"]])]
    await _assert_old_avatar_untouched(client, test_engine, alice, old, new)
