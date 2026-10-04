"""Audit and notification trace for evaluations (#302, #535, R52-R54).

The archived implementation logged audit rows but emitted no notifications
at all — a member was never told an assessment about them had been
published. These tests exist so that does not come back. Since #535 the
audit trail starts when an evaluation is first saved: a draft is its
writer's working copy.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    item_ids,
    publish,
    put_answer,
    transfer,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _audit_actions(db_session: AsyncSession, resource_id: str) -> list[str]:
    """Every audit action recorded against one evaluation."""
    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.resource_type == "evaluation",
            AuditLog.resource_id == resource_id,
        )
    )
    return [row.action for row in result.scalars().all()]


async def _notification_types(client: AsyncClient, token: str) -> list[str]:
    """Notification event types delivered to the caller."""
    response = await client.get("/v1/notifications", headers=auth(token))
    assert response.status_code == 200, response.text
    types: list[str] = []
    for item in response.json()["items"]:
        payload = item.get("payload") or {}
        if isinstance(payload, dict) and payload.get("type"):
            types.append(payload["type"])
    return types


async def _draft(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, str, int]:
    """Admin, coach and alice tokens, and coach's draft about alice."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    return admin_token, coach_token, alice_token, evaluation_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R52")
async def test_should_not_audit_creating_or_editing_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R52 (#535): a draft is a working copy; the trail starts at the first save."""
    admin_token, coach_token, _, evaluation_id = await _draft(client, db_session)
    template_id = (
        await client.get(
            f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
        )
    ).json()["templateId"]
    [item_id] = await item_ids(client, admin_token, template_id)

    written = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=3)
    assert written.status_code == 200, written.text
    period = await client.patch(
        f"/v1/evaluations/by_id/{evaluation_id}",
        json={"periodStartUtc": 1_000, "periodEndUtc": 2_000},
        headers=auth(coach_token),
    )
    assert period.status_code == 200, period.text
    deleted = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert deleted.status_code == 200, deleted.text

    assert await _audit_actions(db_session, str(evaluation_id)) == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R52")
@pytest.mark.requirement("evaluation:R20")
async def test_should_write_audit_row_for_each_lifecycle_move(
    client: AsyncClient, db_session: AsyncSession
):
    """R52: the lifecycle is the part people query the audit log about."""
    _, coach_token, _, evaluation_id = await _draft(client, db_session)

    await publish(client, coach_token, evaluation_id)
    for step in ("unpublish", "revert"):
        moved = await client.post(
            f"/v1/evaluations/by_id/{evaluation_id}/{step}", headers=auth(coach_token)
        )
        assert moved.status_code == 200, moved.text

    actions = await _audit_actions(db_session, str(evaluation_id))
    assert sorted(actions) == [
        "publish_evaluation",
        "revert_evaluation",
        "save_evaluation",
        "unpublish_evaluation",
    ]


@pytest.mark.requirement("notifications:R111")
@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R53")
async def test_should_notify_subject_when_evaluation_published(
    client: AsyncClient, db_session: AsyncSession
):
    """R53: publication tells the member an assessment about them exists."""
    _, coach_token, alice_token, evaluation_id = await _draft(client, db_session)

    await publish(client, coach_token, evaluation_id)

    assert "evaluation.published" in await _notification_types(client, alice_token)


@pytest.mark.requirement("notifications:R111")
@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R38")
async def test_should_not_notify_subject_before_publication(
    client: AsyncClient, db_session: AsyncSession
):
    """R38: a draft is invisible, so it must not announce itself either."""
    _, coach_token, alice_token, evaluation_id = await _draft(client, db_session)
    saved = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )
    assert saved.status_code == 200, saved.text

    assert "evaluation.published" not in await _notification_types(client, alice_token)


@pytest.mark.requirement("notifications:R111")
@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R54")
async def test_should_notify_subject_when_evaluation_withdrawn(
    client: AsyncClient, db_session: AsyncSession
):
    """R54: an assessment that vanishes silently leaves no account of itself."""
    _, coach_token, alice_token, evaluation_id = await _draft(client, db_session)
    await publish(client, coach_token, evaluation_id)

    withdrawn = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/unpublish", headers=auth(coach_token)
    )
    assert withdrawn.status_code == 200, withdrawn.text

    assert "evaluation.withdrawn" in await _notification_types(client, alice_token)


@pytest.mark.requirement("notifications:R111")
@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R53")
@pytest.mark.requirement("evaluation:R52")
async def test_should_notify_receiving_coach_on_transfer(
    client: AsyncClient, db_session: AsyncSession
):
    """R53: the coach who inherits the work is told about it, and R52 logs the move."""
    _, coach_token, _, evaluation_id = await _draft(client, db_session)
    other_token = await create_coach_user(db_session, "othercoach")

    response = await transfer(client, coach_token, evaluation_id, "othercoach")
    assert response.status_code == 204, response.text

    read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(other_token)
    )
    assert read.json()["owner"] == "othercoach"
    assert "evaluation.transferred" in await _notification_types(client, other_token)
    assert await _audit_actions(db_session, str(evaluation_id)) == [
        "transfer_evaluation"
    ]
