"""Evaluation media rules of media_requirements.md not covered elsewhere (#494, #535)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.audit_log import AuditLog

from .evaluation_helpers import create_general_evaluation, create_template, item_ids
from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir", "evaluations_enabled")


async def _evaluation_with_media(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int, str, str]:
    """A coach's evaluation of alice with one clip attached as evidence.

    Returns the coach token, the evaluation id, the evidence tag and the
    media uuid.
    """
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin)
    [item_id] = await item_ids(client, admin, template_id)
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    media = await upload(client, coach)
    attached = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/media",
        json={"tag": str(item_id), "mediaUuid": media["uuid"]},
        headers=auth(coach),
    )
    assert attached.status_code == 201, attached.text
    return coach, evaluation_id, str(item_id), media["uuid"]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R104")
@pytest.mark.requirement("evaluation:R61a")
async def test_should_refuse_every_evaluation_media_operation_when_module_is_off(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    coach, evaluation_id, tag, media_uuid = await _evaluation_with_media(
        client, db_session
    )
    await db_session.commit()
    base = f"/v1/evaluations/by_id/{evaluation_id}/media"
    monkeypatch.setattr(settings, "evaluations_enabled", False)

    for method, url, body in [
        ("GET", base, None),
        ("GET", f"{base}/{tag}", None),
        ("GET", f"{base}/{tag}/{media_uuid}", None),
        ("POST", base, {"tag": tag, "mediaUuid": media_uuid}),
        ("PATCH", f"{base}/{tag}/{media_uuid}", {"metadata": "x"}),
        ("DELETE", f"{base}/{tag}/{media_uuid}", None),
        ("DELETE", f"{base}/{tag}", None),
    ]:
        response = await client.request(method, url, json=body, headers=auth(coach))
        assert response.status_code == 503, (method, url, response.text)
        assert response.json()["detail"]["code"] == "EVALUATIONS_DISABLED"

    monkeypatch.setattr(settings, "evaluations_enabled", True)
    listing = await client.get(base, headers=auth(coach))
    assert [row["media"]["uuid"] for row in listing.json()[tag]] == [media_uuid]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R105")
@pytest.mark.requirement("evaluation:R52")
async def test_should_not_audit_evidence_changes_on_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R52, media R105 (#535): evidence is draft content, outside the audit trail."""
    coach, evaluation_id, tag, media_uuid = await _evaluation_with_media(
        client, db_session
    )
    base = f"/v1/evaluations/by_id/{evaluation_id}/media"

    patched = await client.patch(
        f"{base}/{tag}/{media_uuid}", json={"metadata": "drill 3"}, headers=auth(coach)
    )
    assert patched.status_code == 200
    detached = await client.delete(f"{base}/{tag}/{media_uuid}", headers=auth(coach))
    assert detached.status_code == 204
    reattached = await client.post(
        base, json={"tag": tag, "mediaUuid": media_uuid}, headers=auth(coach)
    )
    assert reattached.status_code == 201
    cleared = await client.delete(f"{base}/{tag}", headers=auth(coach))
    assert cleared.status_code == 204

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource_type == "evaluation",
                    AuditLog.resource_id == str(evaluation_id),
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(rows) == []
    listing = await client.get(base, headers=auth(coach))
    assert listing.json() == {}
