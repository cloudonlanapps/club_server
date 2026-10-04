"""The four permitted lifecycle transitions (#302, R15-R20)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    publish,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R19")
async def test_should_reject_publish_when_evaluation_is_still_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R19: a draft cannot be published directly; it must pass through saved."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/publish", headers=auth(coach_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_TRANSITION"
    read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert read.json()["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R16")
async def test_should_stamp_published_at_when_published(
    client: AsyncClient, db_session: AsyncSession
):
    """R16: publication stamps the time it became visible."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    await publish(client, coach_token, evaluation_id)

    read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert read.json()["status"] == "published"
    assert read.json()["publishedAtUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R17")
@pytest.mark.requirement("evaluation:R20")
async def test_should_clear_published_at_when_withdrawn(
    client: AsyncClient, db_session: AsyncSession
):
    """R20: withdrawal clears the timestamp, so its presence means 'is published'.

    The archived implementation left it set, which is the behaviour this
    test exists to prevent returning.
    """
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    await publish(client, coach_token, evaluation_id)

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/unpublish", headers=auth(coach_token)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert read.json()["publishedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R15")
async def test_should_move_draft_to_saved(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: saving declares the draft complete without changing visibility."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert response.json()["publishedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R18")
async def test_should_revert_saved_to_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R18: saved returns to draft for further editing."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    _ = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/revert", headers=auth(coach_token)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "draft"
    read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert read.json()["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R19")
async def test_should_reject_revert_of_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R19: revert only applies to a saved evaluation."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/revert", headers=auth(coach_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_TRANSITION"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R19")
async def test_should_reject_unpublish_of_a_saved_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R19: withdrawal only applies to something currently published."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    _ = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/unpublish", headers=auth(coach_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_TRANSITION"
