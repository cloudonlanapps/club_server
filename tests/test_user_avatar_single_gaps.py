"""A user has one avatar: a replaced file that something else links (#32).

The old avatar file is also linked from an event, a group, a venue or an
evaluation. Replacing the avatar removes the user's link only: the file and
the other owner's link stay. Media R69b; the rule itself is club_server#28.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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
