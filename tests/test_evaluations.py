"""Evaluation creation, editing and deletion (#302, #535)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    answer_of,
    auth,
    create_general_evaluation,
    create_template,
    get_evaluation,
    item_ids,
    publish,
    put_answer,
)
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _draft(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int]:
    """Admin token, coach token, a draft about alice, and its one item's id."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    [item_id] = await item_ids(client, admin_token, template_id)
    return admin_token, coach_token, evaluation_id, item_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R14")
async def test_should_create_draft_when_coach_creates_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R14: an evaluation is created in draft, with no answers."""
    _, coach_token, evaluation_id, _ = await _draft(client, db_session)

    read = await get_evaluation(client, coach_token, evaluation_id)

    assert read["status"] == "draft"
    assert read["createdBy"] == "coach"
    assert read["owner"] is None
    assert read["answers"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R22")
async def test_should_reject_update_when_evaluation_is_published(
    client: AsyncClient, db_session: AsyncSession
):
    """R22: a published evaluation cannot be edited."""
    _, coach_token, evaluation_id, item_id = await _draft(client, db_session)
    first = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=2)
    assert first.status_code == 200, first.text
    await publish(client, coach_token, evaluation_id)

    response = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=5)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert answer_of(read, item_id)["valueNum"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R22")
async def test_should_reject_editing_a_saved_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R22 (#535): saving declares the content final, so saved is read-only."""
    _, coach_token, evaluation_id, item_id = await _draft(client, db_session)
    saved = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )
    assert saved.status_code == 200, saved.text

    response = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=4)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "saved"
    assert read["answers"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R22")
async def test_should_allow_editing_after_withdrawal_and_revert(
    client: AsyncClient, db_session: AsyncSession
):
    """R22: the named remedy — withdraw, then revert to draft — actually works."""
    _, coach_token, evaluation_id, item_id = await _draft(client, db_session)
    await publish(client, coach_token, evaluation_id)
    for step in ("unpublish", "revert"):
        moved = await client.post(
            f"/v1/evaluations/by_id/{evaluation_id}/{step}", headers=auth(coach_token)
        )
        assert moved.status_code == 200, moved.text

    response = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=3)

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "draft"
    assert answer_of(read, item_id)["valueNum"] == 3


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R24")
async def test_should_reject_delete_when_evaluation_is_not_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R24: only a draft may be deleted."""
    _, coach_token, evaluation_id, _ = await _draft(client, db_session)
    _ = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )

    response = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R25")
async def test_should_soft_delete_and_restore_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R25: deletion is soft, returns the entity, and can be undone."""
    _, coach_token, evaluation_id, _ = await _draft(client, db_session)

    deleted = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert deleted.status_code == 200
    assert deleted.json()["deletedAtUtc"] is not None

    gone = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert gone.status_code == 404

    listed = await client.get("/v1/evaluations/deleted", headers=auth(coach_token))
    assert listed.status_code == 200
    assert [e["id"] for e in listed.json()["items"]] == [evaluation_id]

    restored = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/restore", headers=auth(coach_token)
    )
    assert restored.status_code == 200
    assert restored.json()["deletedAtUtc"] is None
    back = await get_evaluation(client, coach_token, evaluation_id)
    assert back["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R23")
async def test_should_reject_attempt_to_change_the_member_on_update(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: whom an evaluation is about is fixed at creation.

    A draft's event may change (`test_evaluation_draft_event.py`).
    """
    _, coach_token, evaluation_id, _ = await _draft(client, db_session)
    _ = await create_member_user(db_session, "bob")

    response = await client.patch(
        f"/v1/evaluations/by_id/{evaluation_id}",
        json={"createdFor": "bob"},
        headers=auth(coach_token),
    )

    assert response.status_code == 422
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["eventId"] is None
    assert read["createdFor"] == "alice"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R9")
@pytest.mark.requirement("evaluation:R21")
async def test_should_replace_an_answer_when_written_again(
    client: AsyncClient, db_session: AsyncSession
):
    """R9, R21: one answer per question; writing it again replaces it."""
    _, coach_token, evaluation_id, item_id = await _draft(client, db_session)
    first = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=2)
    assert first.status_code == 200, first.text

    response = await put_answer(
        client, coach_token, evaluation_id, item_id, valueNum=5, coachNote="Better"
    )

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert len(read["answers"]) == 1
    assert answer_of(read, item_id)["valueNum"] == 5
    assert answer_of(read, item_id)["coachNote"] == "Better"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R21")
async def test_should_clear_an_answer_in_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R21: an answer can be cleared as well as written."""
    _, coach_token, evaluation_id, item_id = await _draft(client, db_session)
    written = await put_answer(client, coach_token, evaluation_id, item_id, valueNum=4)
    assert written.status_code == 200, written.text

    response = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}/answers/{item_id}",
        headers=auth(coach_token),
    )

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["answers"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
@pytest.mark.requirement("evaluation:R21")
async def test_should_change_the_period_of_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R21: a draft's period is editable, and a general one may carry it (R4)."""
    _, coach_token, evaluation_id, _ = await _draft(client, db_session)

    response = await client.patch(
        f"/v1/evaluations/by_id/{evaluation_id}",
        json={"periodStartUtc": 1_000, "periodEndUtc": 2_000},
        headers=auth(coach_token),
    )

    assert response.status_code == 200, response.text
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert (read["periodStartUtc"], read["periodEndUtc"]) == (1_000, 2_000)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R22")
async def test_should_reject_changing_the_period_once_saved(
    client: AsyncClient, db_session: AsyncSession
):
    """R22: the period is content, read-only once saved."""
    _, coach_token, evaluation_id, _ = await _draft(client, db_session)
    _ = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
    )

    response = await client.patch(
        f"/v1/evaluations/by_id/{evaluation_id}",
        json={"periodStartUtc": 1_000, "periodEndUtc": 2_000},
        headers=auth(coach_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["periodStartUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R21")
@pytest.mark.requirement("evaluation:R35")
async def test_should_hide_a_draft_from_another_coach_trying_to_edit_it(
    client: AsyncClient, db_session: AsyncSession
):
    """R21, R35: only the effective owner edits; for anyone else it does not exist."""
    _, coach_token, evaluation_id, item_id = await _draft(client, db_session)
    other_token = await create_coach_user(db_session, "othercoach")

    response = await put_answer(client, other_token, evaluation_id, item_id, valueNum=1)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["answers"] == []
