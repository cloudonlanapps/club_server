"""Answers, validated against their item, and saving a complete draft (#535).

R9: an answer is a value, a note, evidence — any of them on its own.
R10: every answer is validated against its item's variant.
R15: saving checks required answers and required coach notes.
R22a: clearing an answer detaches its evidence.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    answer_of,
    auth,
    choice_item,
    create_general_evaluation,
    create_template,
    get_evaluation,
    info_item,
    item_ids,
    put_answer,
    qa_item,
    rating_item,
)
from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")

ITEMS: list[dict[str, Any]] = [
    rating_item("Skating"),
    rating_item(
        "Effort",
        rateMin=None,
        rateMax=None,
        rateValues=[
            {"value": 1, "text": "Needs work"},
            {"value": 2, "text": "Good"},
        ],
    ),
    {"type": "yesNo", "question": "Ready to move up?"},
    choice_item("Position"),
    choice_item("Strengths", ("speed", "vision", "grit"), multiple=True),
    {"type": "number", "question": "Goals"},
    qa_item("Summary"),
    info_item(),
]
NAMES = ["rating", "levels", "yesNo", "single", "multiple", "number", "qa", "info"]


async def _draft(
    client: AsyncClient, db_session: AsyncSession, layout: list[Any] | None = None
) -> tuple[str, str, int, dict[str, int]]:
    """Admin and coach tokens, a draft about alice, and item ids by name."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token, layout=layout or ITEMS)
    ids = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    return admin_token, coach_token, evaluation_id, dict(zip(NAMES, ids))


ACCEPTED: list[tuple[str, dict[str, Any], str, Any]] = [
    ("rating", {"valueNum": 5}, "valueNum", 5),
    ("levels", {"valueNum": 2}, "valueNum", 2),
    ("yesNo", {"valueNum": 0}, "valueNum", 0),
    ("single", {"valueText": "defence"}, "valueText", "defence"),
    ("multiple", {"choices": ["grit", "speed"]}, "choices", ["grit", "speed"]),
    ("number", {"valueNum": 2.5}, "valueNum", 2.5),
    ("qa", {"valueText": "**Great** season"}, "valueText", "**Great** season"),
]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R10")
@pytest.mark.parametrize(
    "name,body,field,expected", ACCEPTED, ids=[a[0] for a in ACCEPTED]
)
async def test_should_accept_an_answer_of_its_items_kind(
    client: AsyncClient,
    db_session: AsyncSession,
    name: str,
    body: dict[str, Any],
    field: str,
    expected: Any,
):
    """R10: each question type takes a value of its own kind."""
    _, coach_token, evaluation_id, ids = await _draft(client, db_session)

    response = await put_answer(client, coach_token, evaluation_id, ids[name], **body)

    assert response.status_code == 200, response.text
    answer = answer_of(
        await get_evaluation(client, coach_token, evaluation_id), ids[name]
    )
    assert answer is not None
    value = answer[field]
    assert (sorted(value) if isinstance(value, list) else value) == expected


REFUSED: list[tuple[str, str, dict[str, Any]]] = [
    ("rating_above_range", "rating", {"valueNum": 6}),
    ("rating_fraction", "rating", {"valueNum": 2.5}),
    ("rating_as_text", "rating", {"valueText": "five"}),
    ("level_unknown", "levels", {"valueNum": 3}),
    ("yes_no_two", "yesNo", {"valueNum": 2}),
    ("single_unknown", "single", {"valueText": "goalie"}),
    ("single_as_choices", "single", {"choices": ["forward"]}),
    ("multiple_unknown", "multiple", {"choices": ["speed", "luck"]}),
    ("multiple_repeated", "multiple", {"choices": ["speed", "speed"]}),
    ("multiple_empty", "multiple", {"choices": []}),
    ("number_as_text", "number", {"valueText": "3"}),
    ("qa_as_number", "qa", {"valueNum": 3}),
    ("info", "info", {"valueText": "anything"}),
    ("two_values", "rating", {"valueNum": 3, "valueText": "three"}),
    ("nothing", "rating", {}),
]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R10")
@pytest.mark.parametrize(
    "name,body", [r[1:] for r in REFUSED], ids=[r[0] for r in REFUSED]
)
async def test_should_refuse_an_answer_outside_its_items_domain(
    client: AsyncClient, db_session: AsyncSession, name: str, body: dict[str, Any]
):
    """R10: a wrong kind, a value outside the domain, or no question → INVALID_ANSWER."""
    _, coach_token, evaluation_id, ids = await _draft(client, db_session)

    response = await put_answer(client, coach_token, evaluation_id, ids[name], **body)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_ANSWER"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["answers"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R10")
async def test_should_refuse_an_answer_to_another_templates_item(
    client: AsyncClient, db_session: AsyncSession
):
    """R10: an answer names a question of the evaluation's own template."""
    admin_token, coach_token, evaluation_id, _ = await _draft(client, db_session)
    other_id = await create_template(client, admin_token, name="Other")
    [foreign_id] = await item_ids(client, admin_token, other_id)

    response = await put_answer(
        client, coach_token, evaluation_id, foreign_id, valueNum=3
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ANSWER"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["answers"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R9")
async def test_should_accept_an_answer_that_is_only_a_coach_note(
    client: AsyncClient, db_session: AsyncSession
):
    """R9: an answer may carry only a note."""
    _, coach_token, evaluation_id, ids = await _draft(client, db_session)

    response = await put_answer(
        client, coach_token, evaluation_id, ids["rating"], coachNote="Not seen yet"
    )

    assert response.status_code == 200, response.text
    answer = answer_of(
        await get_evaluation(client, coach_token, evaluation_id), ids["rating"]
    )
    assert answer is not None
    assert (answer["valueNum"], answer["coachNote"]) == (None, "Not seen yet")


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R22a")
@pytest.mark.usefixtures("clean_upload_dir")
async def test_should_detach_evidence_when_an_answer_is_cleared(
    client: AsyncClient, db_session: AsyncSession
):
    """R22a: an answer's evidence goes with it."""
    _, coach_token, evaluation_id, ids = await _draft(client, db_session)
    media = await upload(client, coach_token)
    written = await put_answer(
        client, coach_token, evaluation_id, ids["rating"], valueNum=4
    )
    assert written.status_code == 200, written.text
    attached = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/media",
        json={"tag": str(ids["rating"]), "mediaUuid": media["uuid"]},
        headers=auth(coach_token),
    )
    assert attached.status_code == 201, attached.text
    before = answer_of(
        await get_evaluation(client, coach_token, evaluation_id), ids["rating"]
    )
    assert [e["mediaUuid"] for e in before["evidence"]] == [media["uuid"]]

    response = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}/answers/{ids['rating']}",
        headers=auth(coach_token),
    )

    assert response.status_code == 200, response.text
    listed = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}/media", headers=auth(coach_token)
    )
    assert listed.json() == {}
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["answers"] == []


REQUIRED_LAYOUT = [
    rating_item("Skating", isRequired=True, requireCommentFor=[1, 2]),
    qa_item("Summary"),
]


async def _save(client: AsyncClient, token: str, evaluation_id: int):
    return await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(token)
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R15")
async def test_should_refuse_saving_with_a_required_question_unanswered(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: saving declares the draft complete, so a required gap is refused."""
    _, coach_token, evaluation_id, ids = await _draft(
        client, db_session, layout=REQUIRED_LAYOUT
    )

    response = await _save(client, coach_token, evaluation_id)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INCOMPLETE"
    assert response.json()["detail"]["details"]["itemIds"] == [ids["rating"]]
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R15")
async def test_should_not_count_a_note_alone_as_answering_a_required_question(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: a required question needs a value, not only a note."""
    _, coach_token, evaluation_id, ids = await _draft(
        client, db_session, layout=REQUIRED_LAYOUT
    )
    noted = await put_answer(
        client, coach_token, evaluation_id, ids["rating"], coachNote="Absent"
    )
    assert noted.status_code == 200, noted.text

    response = await _save(client, coach_token, evaluation_id)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INCOMPLETE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R15")
async def test_should_refuse_saving_without_a_coach_note_the_answer_requires(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: a low rating that requires a coach note cannot be saved without one."""
    _, coach_token, evaluation_id, ids = await _draft(
        client, db_session, layout=REQUIRED_LAYOUT
    )
    low = await put_answer(
        client, coach_token, evaluation_id, ids["rating"], valueNum=1
    )
    assert low.status_code == 200, low.text

    response = await _save(client, coach_token, evaluation_id)

    assert response.status_code == 422
    assert response.json()["detail"]["details"]["itemIds"] == [ids["rating"]]
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R15")
async def test_should_save_once_required_answers_and_notes_are_there(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: complete content saves; an optional question may stay empty."""
    _, coach_token, evaluation_id, ids = await _draft(
        client, db_session, layout=REQUIRED_LAYOUT
    )
    answered = await put_answer(
        client,
        coach_token,
        evaluation_id,
        ids["rating"],
        valueNum=1,
        coachNote="Edges need work",
    )
    assert answered.status_code == 200, answered.text

    response = await _save(client, coach_token, evaluation_id)

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "saved"
