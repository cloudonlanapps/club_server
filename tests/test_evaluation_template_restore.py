"""Restoring a template whose name was taken renames it (#71, R49a, R49b)."""

import json
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    get_template,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")

TEMPLATES = "/v1/evaluations/templates"


def _by_id(template_id: int) -> str:
    return f"{TEMPLATES}/by_id/{template_id}"


async def _admin(db_session: AsyncSession) -> str:
    token = await create_admin_user(db_session)
    await db_session.commit()
    return token


async def _deleted(client: AsyncClient, token: str, name: str) -> int:
    """A template of this name, soft-deleted; returns its id."""
    template_id = await create_template(client, token, name=name)
    deleted = await client.delete(_by_id(template_id), headers=auth(token))
    assert deleted.status_code == 200, deleted.text
    return template_id


async def _restore(client: AsyncClient, token: str, template_id: int) -> str:
    """Restore, asserting 200; returns the name the response carries."""
    response = await client.post(f"{_by_id(template_id)}/restore", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["name"]


async def _live_names(client: AsyncClient, token: str) -> list[str]:
    listed = await client.get(TEMPLATES, headers=auth(token))
    assert listed.status_code == 200, listed.text
    return sorted(t["name"] for t in listed.json()["items"])


async def _restore_details(
    db_session: AsyncSession, template_id: int
) -> dict[str, Any]:
    """The details of the one restore audit row against a template."""
    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.resource_type == "evaluation_template",
            AuditLog.resource_id == str(template_id),
            AuditLog.action == "restore_evaluation_template",
        )
    )
    [row] = result.scalars().all()
    return cast(dict[str, Any], json.loads(row.details)) if row.details else {}


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49b")
async def test_should_rename_a_restored_template_when_a_live_one_took_its_name(
    client: AsyncClient, db_session: AsyncSession
):
    """R49b: the restore takes the first free `<name> -- Restored #n`."""
    token = await _admin(db_session)
    old = await _deleted(client, token, "Camp report")
    newer = await create_template(client, token, name="Camp report")

    assert await _restore(client, token, old) == "Camp report -- Restored #1"

    read = await get_template(client, token, old)
    assert (read["name"], read["deletedAtUtc"]) == ("Camp report -- Restored #1", None)
    assert (await get_template(client, token, newer))["name"] == "Camp report"
    assert await _live_names(client, token) == [
        "Camp report",
        "Camp report -- Restored #1",
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49b")
async def test_should_take_the_next_number_when_restored_1_is_taken(
    client: AsyncClient, db_session: AsyncSession
):
    """R49b: `#1` already held by a live template → `#2`."""
    token = await _admin(db_session)
    old = await _deleted(client, token, "Camp report")
    _ = await create_template(client, token, name="Camp report")
    _ = await create_template(client, token, name="Camp report -- Restored #1")

    assert await _restore(client, token, old) == "Camp report -- Restored #2"
    assert (await get_template(client, token, old))["name"] == (
        "Camp report -- Restored #2"
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49a")
@pytest.mark.requirement("evaluation:R49b")
async def test_should_compare_restore_names_ignoring_case_and_whitespace(
    client: AsyncClient, db_session: AsyncSession
):
    """R49a/R49b: the taken name and the taken `#1` differ only in case and spaces."""
    token = await _admin(db_session)
    old = await _deleted(client, token, "Camp report")
    _ = await create_template(client, token, name="  CAMP report ")
    _ = await create_template(client, token, name=" camp REPORT -- restored #1  ")

    assert await _restore(client, token, old) == "Camp report -- Restored #2"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49b")
async def test_should_keep_the_name_when_restoring_a_template_whose_name_is_free(
    client: AsyncClient, db_session: AsyncSession
):
    """R49b: a free name is kept, and the audit row records no rename."""
    token = await _admin(db_session)
    old = await _deleted(client, token, "Camp report")
    _ = await create_template(client, token, name="Term report")

    assert await _restore(client, token, old) == "Camp report"
    assert await _live_names(client, token) == ["Camp report", "Term report"]
    assert "name" not in await _restore_details(db_session, old)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49b")
async def test_should_record_the_rename_in_the_audit_row_when_restore_renames(
    client: AsyncClient, db_session: AsyncSession
):
    """R49b: the restore's audit row carries the old and the new name."""
    token = await _admin(db_session)
    old = await _deleted(client, token, "Camp report")
    _ = await create_template(client, token, name="Camp report")

    _ = await _restore(client, token, old)

    details = await _restore_details(db_session, old)
    assert details["name"] == {
        "old": "Camp report",
        "new": "Camp report -- Restored #1",
    }


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49b")
@pytest.mark.requirement("evaluation:R27")
async def test_should_rename_a_frozen_template_when_restoring_it_onto_a_taken_name(
    client: AsyncClient, db_session: AsyncSession
):
    """R49b/R27: a template in use may be renamed, so its restore renames too."""
    token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    await db_session.commit()
    template_id = await create_template(client, token, name="Camp report")
    evaluation = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    gone = await client.delete(
        f"/v1/evaluations/by_id/{evaluation}", headers=auth(coach_token)
    )
    assert gone.status_code == 200, gone.text
    deleted = await client.delete(_by_id(template_id), headers=auth(token))
    assert deleted.status_code == 200, deleted.text
    _ = await create_template(client, token, name="Camp report")

    assert await _restore(client, token, template_id) == "Camp report -- Restored #1"
    read = await get_template(client, token, template_id)
    assert (read["name"], read["inUse"]) == ("Camp report -- Restored #1", True)
