"""One review per coach, member, template and period (#535, R7)."""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_evaluation_response,
    create_event_with_coach,
    create_general_evaluation,
    create_template,
    get_evaluation,
    mark_attendance,
    past_period,
    transfer,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _setup(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str, int]:
    """Admin and coach tokens, and a template; alice and coach_b exist."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_coach_user(db_session, "coach_b")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    return admin_token, coach_token, template_id


def _duplicate(response: Response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "DUPLICATE_EVALUATION"


async def _own_ids(client: AsyncClient, token: str, deleted: bool = False) -> list[int]:
    path = "/v1/evaluations/deleted" if deleted else "/v1/evaluations"
    listed = await client.get(path, headers=auth(token))
    assert listed.status_code == 200, listed.text
    return [e["id"] for e in listed.json()["items"]]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
@pytest.mark.parametrize(
    "period",
    [{}, past_period(30, 2)],
    ids=["no_period", "same_period"],
)
async def test_should_refuse_a_second_review_of_one_member_template_and_period(
    client: AsyncClient, db_session: AsyncSession, period: dict[str, Any]
):
    """R7: no period counts as a value — one period-less review per coach too."""
    _, coach_token, template_id = await _setup(client, db_session)
    first = await create_general_evaluation(
        client, coach_token, template_id, "alice", **period
    )

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", **period
    )

    _duplicate(response)
    assert await _own_ids(client, coach_token) == [first]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
async def test_should_refuse_a_twin_whatever_its_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R7: the event is not part of the key."""
    admin_token, coach_token, template_id = await _setup(client, db_session)
    event_id, occurrence = await create_event_with_coach(
        client, admin_token, coach_names=["coach"]
    )
    await mark_attendance(client, admin_token, event_id, occurrence, "alice")
    general = await create_general_evaluation(client, coach_token, template_id, "alice")

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    _duplicate(response)
    assert await _own_ids(client, coach_token) == [general]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
async def test_should_accept_reviews_over_other_periods_or_on_other_templates(
    client: AsyncClient, db_session: AsyncSession
):
    """R7: overlapping periods are different periods; templates differ too."""
    admin_token, coach_token, template_id = await _setup(client, db_session)
    other_template = await create_template(client, admin_token, name="Term report")

    created = [
        await create_evaluation_response(client, coach_token, template_id, "alice"),
        await create_evaluation_response(
            client, coach_token, template_id, "alice", **past_period(30, 5)
        ),
        await create_evaluation_response(
            client, coach_token, template_id, "alice", **past_period(20, 1)
        ),
        await create_evaluation_response(client, coach_token, other_template, "alice"),
    ]

    assert [r.status_code for r in created] == [201] * len(created)
    assert len(await _own_ids(client, coach_token)) == len(created)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
async def test_should_let_two_coaches_each_review_one_member_on_one_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R7: the key is per effective owner."""
    _, coach_token, template_id = await _setup(client, db_session)
    other_token = await create_coach_user(db_session, "coach_c")
    _ = await create_general_evaluation(client, coach_token, template_id, "alice")

    response = await create_evaluation_response(
        client, other_token, template_id, "alice"
    )

    assert response.status_code == 201, response.text
    assert await _own_ids(client, other_token) == [response.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
async def test_should_not_count_a_deleted_review(
    client: AsyncClient, db_session: AsyncSession
):
    """R7: only live evaluations hold the key."""
    _, coach_token, template_id = await _setup(client, db_session)
    first = await create_general_evaluation(client, coach_token, template_id, "alice")
    deleted = await client.delete(
        f"/v1/evaluations/by_id/{first}", headers=auth(coach_token)
    )
    assert deleted.status_code == 200, deleted.text

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice"
    )

    assert response.status_code == 201, response.text
    assert await _own_ids(client, coach_token) == [response.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
@pytest.mark.requirement("evaluation:R25")
async def test_should_refuse_restoring_a_review_whose_twin_exists(
    client: AsyncClient, db_session: AsyncSession
):
    """R7: a restore may not bring back a second review of one key."""
    _, coach_token, template_id = await _setup(client, db_session)
    first = await create_general_evaluation(client, coach_token, template_id, "alice")
    deleted = await client.delete(
        f"/v1/evaluations/by_id/{first}", headers=auth(coach_token)
    )
    assert deleted.status_code == 200, deleted.text
    second = await create_general_evaluation(client, coach_token, template_id, "alice")

    response = await client.post(
        f"/v1/evaluations/by_id/{first}/restore", headers=auth(coach_token)
    )

    _duplicate(response)
    assert await _own_ids(client, coach_token) == [second]
    assert await _own_ids(client, coach_token, deleted=True) == [first]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
@pytest.mark.requirement("evaluation:R36")
async def test_should_refuse_transfer_to_a_coach_who_owns_its_twin(
    client: AsyncClient, db_session: AsyncSession
):
    """R7, R36: the receiving coach may not end up holding two of one key."""
    _, coach_token, template_id = await _setup(client, db_session)
    other_token = await create_coach_user(db_session, "coach_d")
    mine = await create_general_evaluation(client, coach_token, template_id, "alice")
    _ = await create_general_evaluation(client, other_token, template_id, "alice")

    response = await transfer(client, coach_token, mine, "coach_d")

    _duplicate(response)
    read = await get_evaluation(client, coach_token, mine)
    assert read["owner"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
@pytest.mark.requirement("evaluation:R23")
async def test_should_refuse_moving_a_drafts_period_onto_its_twin(
    client: AsyncClient, db_session: AsyncSession
):
    """R7, R23: changing a draft's period may not make it a twin."""
    _, coach_token, template_id = await _setup(client, db_session)
    period = past_period(30, 2)
    _ = await create_general_evaluation(
        client, coach_token, template_id, "alice", **period
    )
    other = await create_general_evaluation(client, coach_token, template_id, "alice")

    response = await client.patch(
        f"/v1/evaluations/by_id/{other}", json=period, headers=auth(coach_token)
    )

    _duplicate(response)
    read = await get_evaluation(client, coach_token, other)
    assert (read["periodStartUtc"], read["periodEndUtc"]) == (None, None)
