"""Transferring an evaluation to another coach (#302, #535, R36, R37)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_evaluation_response,
    create_event_with_coach,
    create_general_evaluation,
    create_template,
    get_evaluation,
    mark_attendance,
    publish,
    transfer,
)
from .helpers import (
    create_admin_user,
    create_coach_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _general_draft(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, str, int]:
    """Admin, coach and othercoach tokens, and coach's draft about alice."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    other_token = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    return admin_token, coach_token, other_token, evaluation_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R37")
async def test_should_reject_transfer_to_a_coach_who_fails_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    """R37: transfer is not a way around the scope rules."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_coach_user(db_session, "stranger")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token, name="Event report")
    event_id, occurrence = await create_event_with_coach(
        client, admin_token, coach_names=["coach"]
    )
    await mark_attendance(client, admin_token, event_id, occurrence, "alice")
    created = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )
    assert created.status_code == 201, created.text
    evaluation_id = created.json()["id"]

    response = await transfer(client, coach_token, evaluation_id, "stranger")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_ELIGIBLE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["owner"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R36")
async def test_should_reject_transfer_of_published_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R36: transfer is a pre-publication move only."""
    _, coach_token, _, evaluation_id = await _general_draft(client, db_session)
    await publish(client, coach_token, evaluation_id)

    response = await transfer(client, coach_token, evaluation_id, "othercoach")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["owner"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R36")
@pytest.mark.requirement("evaluation:R8")
async def test_should_let_the_owning_coach_transfer_their_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R36 (#535): the effective owner hands it on; who created it never changes."""
    _, coach_token, other_token, evaluation_id = await _general_draft(
        client, db_session
    )

    response = await transfer(client, coach_token, evaluation_id, "othercoach")

    assert response.status_code == 204, response.text
    read = await get_evaluation(client, other_token, evaluation_id)
    assert (read["createdBy"], read["owner"]) == ("coach", "othercoach")
    gone = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert gone.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R36")
@pytest.mark.parametrize("who", ["super_admin", "plain_admin"])
async def test_should_let_an_admin_transfer_by_id_without_seeing_content(
    client: AsyncClient, db_session: AsyncSession, who: str
):
    """R36: an admin who names the evaluation by id may transfer it, and sees nothing."""
    admin_token, _, other_token, evaluation_id = await _general_draft(
        client, db_session
    )
    plain_admin = await create_regular_admin_user(db_session, "plainadmin")
    token = admin_token if who == "super_admin" else plain_admin

    response = await transfer(client, token, evaluation_id, "othercoach")

    assert response.status_code == 204, response.text
    assert response.content == b""
    read = await get_evaluation(client, other_token, evaluation_id)
    assert read["owner"] == "othercoach"
    listed = await client.get("/v1/evaluations", headers=auth(token))
    assert listed.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R36")
@pytest.mark.requirement("evaluation:R35")
async def test_should_hide_evaluation_from_a_coach_who_tries_to_transfer_it(
    client: AsyncClient, db_session: AsyncSession
):
    """R36, R35: a coach who does not own it cannot move it, nor learn it exists."""
    _, coach_token, _, evaluation_id = await _general_draft(client, db_session)
    intruder = await create_coach_user(db_session, "intruder")

    response = await transfer(client, intruder, evaluation_id, "intruder")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["owner"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R37")
@pytest.mark.requirement("evaluation:R45")
@pytest.mark.parametrize("target", ["bob", "plainadmin"])
async def test_should_reject_transfer_to_someone_without_the_coach_role(
    client: AsyncClient, db_session: AsyncSession, target: str
):
    """R45 (#488, #535): the owner must hold the coach role — a member or admin does not."""
    _, coach_token, _, evaluation_id = await _general_draft(client, db_session)
    _ = await create_member_user(db_session, "bob")
    _ = await create_regular_admin_user(db_session, "plainadmin")

    response = await transfer(client, coach_token, evaluation_id, target)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_ELIGIBLE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["owner"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R28")
@pytest.mark.requirement("evaluation:R45")
async def test_should_accept_an_admin_who_also_coaches_as_a_writer(
    client: AsyncClient, db_session: AsyncSession
):
    """R45: the coach role qualifies even when the user is an admin as well."""
    admin_token = await create_admin_user(db_session)
    both_token = await create_coach_admin_user(db_session, "coachadmin")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)

    response = await create_evaluation_response(
        client, both_token, template_id, "alice"
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, both_token, response.json()["id"])
    assert read["createdBy"] == "coachadmin"
