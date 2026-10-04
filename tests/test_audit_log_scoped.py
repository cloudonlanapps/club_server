"""Tests for #253 (Part 2) — entity-scoped audit-log queries.

`GET /v1/audit_log` gains scope params:
- `username` — rows where the user is actor *or* target (verbose has no effect).
- `resource_type` + `resource_id` — rows for one entity, widened by `verbose`:
  1 = the entity's own rows, 2 = also its media-link rows, 3 = also its
  occurrence rows (events only).

Authorization splits by mode: the unscoped global feed stays super-admin only,
while scoped queries are open to any admin or coach (ownership-independent, for
transparency).
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from tests.helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

ENDPOINT = "/v1/audit_log"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _insert_audit(
    db: AsyncSession,
    *,
    timestamp: int,
    action: str,
    actor_username: str | None = None,
    target_username: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        timestamp=timestamp,
        actor_username=actor_username,
        target_username=target_username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=json.dumps(details) if details is not None else None,
    )
    db.add(entry)
    await db.flush()
    return entry


async def _seed(db: AsyncSession) -> None:
    # bob as actor
    await _insert_audit(db, timestamp=100, action="login", actor_username="bob")
    # bob as target
    await _insert_audit(
        db,
        timestamp=101,
        action="block_user",
        actor_username="adm",
        target_username="bob",
    )
    # unrelated user
    await _insert_audit(db, timestamp=102, action="login", actor_username="zoe")
    # event 42 own row
    await _insert_audit(
        db,
        timestamp=103,
        action="cancel_event",
        actor_username="adm",
        resource_type="event",
        resource_id="42",
    )
    # media-link owned by event 42 (composite resource_id <id>:<tag>:<uuid>)
    await _insert_audit(
        db,
        timestamp=104,
        action="create_event_media_link",
        actor_username="adm",
        resource_type="event_media_link",
        resource_id="42:cover:uuid-1",
    )
    # tag-deletion owned by event 42 (resource_id <id>:<tag>, #260)
    await _insert_audit(
        db,
        timestamp=105,
        action="delete_event_media_tag",
        actor_username="adm",
        resource_type="event_media_tag",
        resource_id="42:cover",
    )
    # occurrence of event 42 (composite resource_id <id>:<time>)
    await _insert_audit(
        db,
        timestamp=106,
        action="cancel_occurrence",
        actor_username="adm",
        resource_type="occurrence",
        resource_id="42:1700000000000",
    )
    # different event
    await _insert_audit(
        db,
        timestamp=107,
        action="cancel_event",
        actor_username="adm",
        resource_type="event",
        resource_id="99",
    )


# --- Authorization -----------------------------------------------------------


@pytest.mark.requirement("platform:R18")
@pytest.mark.asyncio
async def test_global_feed_still_super_admin_only(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session)
    coach = await create_coach_user(db_session, username="c1")
    # Commit so the 403's session rollback (in the get_db override) does not
    # wipe these users between the two requests.
    await db_session.commit()
    for token in (admin, coach):
        resp = await client.get(ENDPOINT, headers=auth(token))
        assert resp.status_code == 403, resp.text


@pytest.mark.requirement("platform:R19")
@pytest.mark.asyncio
async def test_scoped_query_allowed_for_admin_and_coach(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    admin = await create_regular_admin_user(db_session)
    coach = await create_coach_user(db_session, username="c1")
    for token in (admin, coach):
        resp = await client.get(
            ENDPOINT, params={"username": "bob"}, headers=auth(token)
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.requirement("platform:R20")
@pytest.mark.asyncio
async def test_scoped_query_forbidden_for_member(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_member_user(db_session)
    resp = await client.get(ENDPOINT, params={"username": "bob"}, headers=auth(token))
    assert resp.status_code == 403, resp.text


@pytest.mark.requirement("platform:R19")
@pytest.mark.asyncio
async def test_super_admin_can_use_both_modes(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_admin_user(db_session)
    assert (await client.get(ENDPOINT, headers=auth(token))).status_code == 200
    resp = await client.get(ENDPOINT, params={"username": "bob"}, headers=auth(token))
    assert resp.status_code == 200, resp.text


# --- Filtering ---------------------------------------------------------------


@pytest.mark.requirement("platform:R19")
@pytest.mark.asyncio
async def test_username_matches_actor_or_target(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_regular_admin_user(db_session)
    resp = await client.get(ENDPOINT, params={"username": "bob"}, headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    actions = {r["action"] for r in body["rows"]}
    assert body["total"] == 2
    assert actions == {"login", "block_user"}  # bob as actor + as target


@pytest.mark.requirement("platform:R19")
@pytest.mark.asyncio
async def test_resource_scope_matches_only_that_entity(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_coach_user(db_session, username="c1")
    resp = await client.get(
        ENDPOINT,
        params={"resource_type": "event", "resource_id": "42"},
        headers=auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # event 42 only — not the occurrence (composite id) and not event 99
    assert body["total"] == 1
    assert body["rows"][0]["resource"]["id"] == 42
    assert body["rows"][0]["action"] == "cancel_event"


@pytest.mark.requirement("platform:R20")
@pytest.mark.asyncio
async def test_resource_type_without_id_is_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_regular_admin_user(db_session)
    resp = await client.get(
        ENDPOINT, params={"resource_type": "event"}, headers=auth(token)
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_RESOURCE_SCOPE"


@pytest.mark.requirement("platform:R20")
@pytest.mark.asyncio
async def test_resource_id_without_type_is_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_regular_admin_user(db_session)
    resp = await client.get(ENDPOINT, params={"resource_id": "42"}, headers=auth(token))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_RESOURCE_SCOPE"


# --- Verbose widening --------------------------------------------------------


async def _event42(client: AsyncClient, token: str, verbose: int | None = None) -> dict:
    params: dict[str, object] = {"resource_type": "event", "resource_id": "42"}
    if verbose is not None:
        params["verbose"] = verbose
    resp = await client.get(ENDPOINT, params=params, headers=auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.requirement("platform:R21")
@pytest.mark.asyncio
async def test_verbose_default_is_own_rows_only(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_regular_admin_user(db_session)
    body = await _event42(client, token)  # omitted → default 1
    assert body["total"] == 1
    assert {r["action"] for r in body["rows"]} == {"cancel_event"}


@pytest.mark.requirement("platform:R21")
@pytest.mark.asyncio
async def test_verbose_2_adds_media_links(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_regular_admin_user(db_session)
    body = await _event42(client, token, verbose=2)
    # own row + media-link row + tag-deletion row (#260)
    assert body["total"] == 3
    assert {r["action"] for r in body["rows"]} == {
        "cancel_event",
        "create_event_media_link",
        "delete_event_media_tag",
    }


@pytest.mark.requirement("platform:R21")
@pytest.mark.asyncio
async def test_verbose_3_adds_occurrences(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_regular_admin_user(db_session)
    body = await _event42(client, token, verbose=3)
    # all four event-42 rows; event 99 (also cancel_event) is excluded —
    # were it included, there would be a second cancel_event row.
    assert body["total"] == 4
    assert {r["action"] for r in body["rows"]} == {
        "cancel_event",
        "create_event_media_link",
        "delete_event_media_tag",
        "cancel_occurrence",
    }


@pytest.mark.requirement("platform:R21")
@pytest.mark.asyncio
async def test_verbose_out_of_range_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_regular_admin_user(db_session)
    # Commit so the first 422's session rollback doesn't wipe the user before
    # the second loop iteration.
    await db_session.commit()
    for bad in (0, 4):
        resp = await client.get(
            ENDPOINT,
            params={"resource_type": "event", "resource_id": "42", "verbose": bad},
            headers=auth(token),
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.requirement("platform:R21")
@pytest.mark.asyncio
async def test_verbose_ignored_for_username_scope(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(db_session)
    token = await create_regular_admin_user(db_session)
    resp = await client.get(
        ENDPOINT, params={"username": "bob", "verbose": 3}, headers=auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 2  # actor + target rows, unaffected by verbose
