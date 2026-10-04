"""Restore and hard delete of an item in the wrong state (#520, #526).

Restoring an item that is not soft-deleted → 422 `NOTHING_TO_RESTORE`;
hard-deleting an item that is not soft-deleted → 422
`HARD_DELETE_NEEDS_SOFT_DELETE`. The same two codes on every endpoint, and
the item is left exactly as it was.
"""

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import create_general_evaluation, create_template
from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)
from .redesign_helpers import create_camp, create_venue

NOTHING_TO_RESTORE = "NOTHING_TO_RESTORE"
HARD_DELETE_NEEDS_SOFT_DELETE = "HARD_DELETE_NEEDS_SOFT_DELETE"


async def _read(client: AsyncClient, token: str, url: str) -> dict:
    response = await client.get(url, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _assert_refused_and_unchanged(
    client: AsyncClient,
    token: str,
    read_url: str,
    call: Callable[[], Awaitable[Response]],
    code: str,
) -> None:
    before = await _read(client, token, read_url)
    assert before["deletedAtUtc"] is None

    response = await call()

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == code
    assert await _read(client, token, read_url) == before


async def _live_group(client: AsyncClient, token: str) -> int:
    response = await client.post(
        "/v1/groups", json={"name": "Live group"}, headers=auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _restore(client: AsyncClient, token: str, url: str) -> Response:
    return await client.post(f"{url}/restore", headers=auth(token))


async def _hard_delete(client: AsyncClient, token: str, url: str) -> Response:
    return await client.delete(f"{url}/hard", headers=auth(token))


# --------------------------------------------------------------------------
# Restore of a live item → 422 NOTHING_TO_RESTORE
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("camps:R7a")
async def test_should_refuse_restore_when_event_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    camp = await create_camp(client, admin, await create_venue(client, admin))
    url = f"/v1/events/by_id/{camp['id']}"

    await _assert_refused_and_unchanged(
        client, admin, url, lambda: _restore(client, admin, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.requirement("users:R53")
async def test_should_refuse_restore_when_user_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()
    url = "/v1/users/by_id/alice"

    await _assert_refused_and_unchanged(
        client, admin, url, lambda: _restore(client, admin, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R15a")
async def test_should_refuse_restore_when_venue_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    url = f"/v1/venues/by_id/{await create_venue(client, admin)}"

    await _assert_refused_and_unchanged(
        client, admin, url, lambda: _restore(client, admin, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R14a")
async def test_should_refuse_restore_when_group_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    url = f"/v1/groups/by_id/{await _live_group(client, admin)}"

    await _assert_refused_and_unchanged(
        client, admin, url, lambda: _restore(client, admin, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
@pytest.mark.requirement("evaluation:R26")
async def test_should_refuse_restore_when_template_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    url = f"/v1/evaluations/templates/by_id/{await create_template(client, admin)}"

    await _assert_refused_and_unchanged(
        client, admin, url, lambda: _restore(client, admin, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
@pytest.mark.requirement("evaluation:R25")
async def test_should_refuse_restore_when_evaluation_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin)
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    url = f"/v1/evaluations/by_id/{evaluation_id}"

    # Only the owning coach sees an evaluation, so the coach reads and restores.
    await _assert_refused_and_unchanged(
        client, coach, url, lambda: _restore(client, coach, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.requirement("media:R50")
@pytest.mark.usefixtures("clean_upload_dir")
async def test_should_refuse_restore_when_media_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    url = f"/v1/media/by_id/{record['id']}"

    await _assert_refused_and_unchanged(
        client, admin, url, lambda: _restore(client, admin, url), NOTHING_TO_RESTORE
    )


@pytest.mark.asyncio
@pytest.mark.requirement("media:R50")
@pytest.mark.usefixtures("clean_upload_dir")
async def test_should_write_no_audit_row_when_restoring_live_media(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)

    refused = await _restore(client, admin, f"/v1/media/by_id/{record['id']}")

    assert refused.status_code == 422, refused.text
    audit = await client.get(
        "/v1/audit_log",
        params={"resource_type": "media", "resource_id": str(record["id"])},
        headers=auth(admin),
    )
    assert audit.status_code == 200, audit.text
    actions = [row["action"] for row in audit.json()["rows"]]
    assert actions == ["upload_media_v2"], actions


# --------------------------------------------------------------------------
# Hard delete of a live item → 422 HARD_DELETE_NEEDS_SOFT_DELETE
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("camps:R10")
async def test_should_refuse_hard_delete_when_event_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    camp = await create_camp(client, admin, await create_venue(client, admin))
    url = f"/v1/events/by_id/{camp['id']}"

    await _assert_refused_and_unchanged(
        client,
        admin,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("users:R55")
async def test_should_refuse_hard_delete_when_user_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()
    url = "/v1/users/by_id/alice"

    await _assert_refused_and_unchanged(
        client,
        admin,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R13")
async def test_should_refuse_hard_delete_when_venue_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    url = f"/v1/venues/by_id/{await create_venue(client, admin)}"

    await _assert_refused_and_unchanged(
        client,
        admin,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R17")
async def test_should_refuse_hard_delete_when_group_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    url = f"/v1/groups/by_id/{await _live_group(client, admin)}"

    await _assert_refused_and_unchanged(
        client,
        admin,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
@pytest.mark.requirement("evaluation:R26")
async def test_should_refuse_hard_delete_when_template_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    url = f"/v1/evaluations/templates/by_id/{await create_template(client, admin)}"

    await _assert_refused_and_unchanged(
        client,
        admin,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
@pytest.mark.requirement("evaluation:R25")
async def test_should_refuse_hard_delete_when_evaluation_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin)
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    url = f"/v1/evaluations/by_id/{evaluation_id}"

    # The super admin hard-deletes; only the owning coach can read it back.
    await _assert_refused_and_unchanged(
        client,
        coach,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("media:R52")
@pytest.mark.usefixtures("clean_upload_dir")
async def test_should_refuse_hard_delete_when_media_is_not_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    record = await upload(client, admin)
    url = f"/v1/media/by_id/{record['id']}"

    await _assert_refused_and_unchanged(
        client,
        admin,
        url,
        lambda: _hard_delete(client, admin, url),
        HARD_DELETE_NEEDS_SOFT_DELETE,
    )
