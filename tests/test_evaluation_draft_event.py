"""A draft's event may change; its member may not (#535, R23)."""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_event_with_coach,
    create_general_evaluation,
    create_template,
    days_ago,
    get_evaluation,
    item_ids,
    mark_attendance,
    put_answer,
    record_past_attendance,
    transfer,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _setup(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int]:
    """Admin and coach tokens, a template, and an event coach coaches and alice attends."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    event_id, occurrence = await create_event_with_coach(
        client, admin_token, coach_names=["coach"]
    )
    await mark_attendance(client, admin_token, event_id, occurrence, "alice")
    return admin_token, coach_token, template_id, event_id


async def _patch(
    client: AsyncClient, token: str, evaluation_id: int, body: dict[str, Any]
) -> Response:
    return await client.patch(
        f"/v1/evaluations/by_id/{evaluation_id}", json=body, headers=auth(token)
    )


def _refused(response: Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["detail"]["code"] == code


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
async def test_should_move_a_general_draft_onto_an_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: a draft's event may change when eligibility holds for it."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await _patch(client, coach_token, evaluation_id, {"eventId": event_id})

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert (read["eventId"], read["periodStartUtc"]) == (event_id, None)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
async def test_should_make_an_event_draft_general_with_a_null_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: `eventId: null` makes an event draft general."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    response = await _patch(client, coach_token, evaluation_id, {"eventId": None})

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R33")
async def test_should_keep_the_event_when_only_the_period_changes(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: a period-only change leaves the event as it was."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    attended = days_ago(3)
    await record_past_attendance(db_session, event_id, "alice", attended)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    response = await _patch(
        client,
        coach_token,
        evaluation_id,
        {"periodStartUtc": days_ago(7), "periodEndUtc": days_ago(1)},
    )

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] == event_id
    assert read["periodStartUtc"] < attended < read["periodEndUtc"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R33")
async def test_should_change_the_event_and_period_together(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: eligibility is checked against the resulting event and period."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    await record_past_attendance(db_session, event_id, "alice", days_ago(3))
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    period = {"periodStartUtc": days_ago(7), "periodEndUtc": days_ago(1)}

    response = await _patch(
        client, coach_token, evaluation_id, {"eventId": event_id, **period}
    )

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert (read["eventId"], read["periodStartUtc"], read["periodEndUtc"]) == (
        event_id,
        period["periodStartUtc"],
        period["periodEndUtc"],
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R33")
async def test_should_refuse_an_event_the_member_has_no_attendance_for_in_the_period(
    client: AsyncClient, db_session: AsyncSession
):
    """R23, R33: the member half is checked within the draft's existing period."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    evaluation_id = await create_general_evaluation(
        client,
        coach_token,
        template_id,
        "alice",
        periodStartUtc=days_ago(30),
        periodEndUtc=days_ago(20),
    )

    response = await _patch(client, coach_token, evaluation_id, {"eventId": event_id})

    _refused(response, 422, "NOT_ELIGIBLE")
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R32")
async def test_should_refuse_an_event_the_owner_does_not_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R23, R32: the coach half applies to the new event.

    alice attended the event, but the draft's owner is not on its coach list.
    """
    _, _, template_id, event_id = await _setup(client, db_session)
    stranger_token = await create_coach_user(db_session, "stranger")
    evaluation_id = await create_general_evaluation(
        client, stranger_token, template_id, "alice"
    )

    response = await _patch(
        client, stranger_token, evaluation_id, {"eventId": event_id}
    )

    _refused(response, 422, "NOT_ELIGIBLE")
    read = await get_evaluation(client, stranger_token, evaluation_id)
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R3")
async def test_should_refuse_an_unknown_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R23, R3: the new event must exist."""
    _, coach_token, template_id, _ = await _setup(client, db_session)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await _patch(client, coach_token, evaluation_id, {"eventId": 999_999})

    _refused(response, 404, "EVENT_NOT_FOUND")
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R22")
async def test_should_refuse_changing_the_event_once_saved(
    client: AsyncClient, db_session: AsyncSession
):
    """R23, R22: only a draft's event may change."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    saved = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )
    assert saved.status_code == 200, saved.text

    response = await _patch(client, coach_token, evaluation_id, {"eventId": event_id})

    _refused(response, 422, "INVALID_STATE")
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
@pytest.mark.requirement("evaluation:R37")
async def test_should_check_the_new_event_against_the_effective_owner(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: after a transfer it is the new owner who must coach the event."""
    _, coach_token, template_id, event_id = await _setup(client, db_session)
    new_owner = await create_coach_user(db_session, "coach_b")
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    moved = await transfer(client, coach_token, evaluation_id, "coach_b")
    assert moved.status_code == 204, moved.text

    response = await _patch(client, new_owner, evaluation_id, {"eventId": event_id})

    _refused(response, 422, "NOT_ELIGIBLE")
    read = await get_evaluation(client, new_owner, evaluation_id)
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
async def test_should_keep_the_answers_when_the_event_changes(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: the template does not change, so the answers stay."""
    admin_token, coach_token, template_id, event_id = await _setup(client, db_session)
    [item_id] = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    written = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=4)
    assert written.status_code == 200, written.text

    response = await _patch(client, coach_token, evaluation_id, {"eventId": event_id})

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] == event_id
    assert [(a["itemId"], a["valueNum"]) for a in read["answers"]] == [(item_id, 4)]
