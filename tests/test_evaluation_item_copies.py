"""Copying a question between templates, and finding one to copy (#535).

R12b: a copy records its origin and keeps its answer domain.
R46a: staff search the items of every template by text and type.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    choice_item,
    create_template,
    get_template,
    item_ids,
    qa_item,
    rating_item,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _source(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int, int, int]:
    """Admin token; a source template with a rating; a target template; the rating id."""
    admin_token = await create_admin_user(db_session)
    source_id = await create_template(
        client,
        admin_token,
        name="Autumn",
        layout=[rating_item("Forward stride"), choice_item("Position")],
    )
    target_id = await create_template(client, admin_token, name="Spring")
    [rating_id, _] = await item_ids(client, admin_token, source_id)
    return admin_token, source_id, target_id, rating_id


async def _add(
    client: AsyncClient, admin_token: str, template_id: int, item: dict[str, object]
):
    return await client.post(
        f"/v1/evaluations/templates/by_id/{template_id}/items",
        json={"item": item},
        headers=auth(admin_token),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12b")
async def test_should_record_the_origin_of_a_copied_question(
    client: AsyncClient, db_session: AsyncSession
):
    """R12b: a copy is a new item of its own that points at its origin."""
    admin_token, _, target_id, rating_id = await _source(client, db_session)

    response = await _add(
        client,
        admin_token,
        target_id,
        rating_item("Forward stride (spring)", originItemId=rating_id),
    )

    assert response.status_code == 201, response.text
    copy = (await get_template(client, admin_token, target_id))["items"][-1]
    assert copy["id"] != rating_id
    assert copy["originItemId"] == rating_id
    assert copy["question"] == "Forward stride (spring)"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12b")
async def test_should_point_a_copy_of_a_copy_at_the_first_item(
    client: AsyncClient, db_session: AsyncSession
):
    """R12b: origins never chain, so comparison is one join."""
    admin_token, _, target_id, rating_id = await _source(client, db_session)
    first = await _add(
        client, admin_token, target_id, rating_item(originItemId=rating_id)
    )
    assert first.status_code == 201, first.text
    copy_id = (await get_template(client, admin_token, target_id))["items"][-1]["id"]
    third_id = await create_template(client, admin_token, name="Summer")

    response = await _add(
        client, admin_token, third_id, rating_item(originItemId=copy_id)
    )

    assert response.status_code == 201, response.text
    copy_of_copy = (await get_template(client, admin_token, third_id))["items"][-1]
    assert copy_of_copy["originItemId"] == rating_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12b")
@pytest.mark.parametrize(
    "item",
    [
        rating_item("Forward stride", rateMin=1, rateMax=10),
        rating_item(
            "Forward stride",
            rateMin=None,
            rateMax=None,
            rateValues=[{"value": 1, "text": "Low"}, {"value": 2, "text": "High"}],
        ),
        qa_item("Forward stride"),
    ],
    ids=["other_range", "levels", "other_type"],
)
async def test_should_refuse_a_copy_that_changes_the_answer_domain(
    client: AsyncClient, db_session: AsyncSession, item: dict[str, object]
):
    """R12b: the same question keeps its type and the same scale."""
    admin_token, _, target_id, rating_id = await _source(client, db_session)

    response = await _add(
        client, admin_token, target_id, {**item, "originItemId": rating_id}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ORIGIN_MISMATCH"
    read = await get_template(client, admin_token, target_id)
    assert len(read["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12b")
async def test_should_refuse_a_copied_choice_with_other_choice_values(
    client: AsyncClient, db_session: AsyncSession
):
    """R12b: a choice copy keeps the same choice values; their labels may differ."""
    admin_token, source_id, target_id, _ = await _source(client, db_session)
    [_, choice_id] = await item_ids(client, admin_token, source_id)
    relabelled = choice_item("Where do you play?", originItemId=choice_id)
    relabelled["choices"] = [
        {"value": "forward", "text": "Up front"},
        {"value": "defence", "text": "At the back"},
    ]

    accepted = await _add(client, admin_token, target_id, relabelled)
    refused = await _add(
        client,
        admin_token,
        target_id,
        choice_item("Position", ("forward", "goalie"), originItemId=choice_id),
    )

    assert accepted.status_code == 201, accepted.text
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "ORIGIN_MISMATCH"
    read = await get_template(client, admin_token, target_id)
    assert [i["question"] for i in read["items"]] == ["Skating", "Where do you play?"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12b")
async def test_should_refuse_editing_a_copy_away_from_its_origins_domain(
    client: AsyncClient, db_session: AsyncSession
):
    """R12b: the rule holds on every write of a copy, not only the first."""
    admin_token, _, target_id, rating_id = await _source(client, db_session)
    added = await _add(
        client, admin_token, target_id, rating_item(originItemId=rating_id)
    )
    assert added.status_code == 201, added.text
    copy_id = (await get_template(client, admin_token, target_id))["items"][-1]["id"]

    response = await client.put(
        f"/v1/evaluations/templates/by_id/{target_id}/items/{copy_id}",
        json=rating_item("Forward stride", rateMin=0, rateMax=5),
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ORIGIN_MISMATCH"
    copy = (await get_template(client, admin_token, target_id))["items"][-1]
    assert (copy["rateMin"], copy["originItemId"]) == (1, rating_id)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12b")
async def test_should_answer_404_for_a_copy_of_an_unknown_item(
    client: AsyncClient, db_session: AsyncSession
):
    """R12b: an origin must be an item that exists."""
    admin_token, _, target_id, _ = await _source(client, db_session)

    response = await _add(
        client, admin_token, target_id, rating_item(originItemId=999_999)
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ITEM_NOT_FOUND"
    read = await get_template(client, admin_token, target_id)
    assert len(read["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46a")
async def test_should_let_staff_search_items_of_every_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R46a: search by text across templates, naming the template each came from."""
    admin_token, source_id, target_id, rating_id = await _source(client, db_session)
    coach_token = await create_coach_user(db_session, "coach")

    for token in (admin_token, coach_token):
        response = await client.get(
            "/v1/evaluations/templates/items",
            params={"search": "stride"},
            headers=auth(token),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        [hit] = body["items"]
        assert (hit["templateId"], hit["templateName"]) == (source_id, "Autumn")
        assert (hit["item"]["id"], hit["item"]["question"]) == (
            rating_id,
            "Forward stride",
        )
    assert target_id != source_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46a")
async def test_should_filter_item_search_by_type(
    client: AsyncClient, db_session: AsyncSession
):
    """R46a: the search narrows to one question type."""
    admin_token, source_id, _, _ = await _source(client, db_session)
    [_, choice_id] = await item_ids(client, admin_token, source_id)

    response = await client.get(
        "/v1/evaluations/templates/items",
        params={"type": "singleChoice"},
        headers=auth(admin_token),
    )

    assert response.status_code == 200, response.text
    assert [hit["item"]["id"] for hit in response.json()["items"]] == [choice_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46a")
async def test_should_refuse_item_search_to_a_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R46a: the item search is a staff read."""
    _ = await _source(client, db_session)
    member_token = await create_member_user(db_session, "alice")

    response = await client.get(
        "/v1/evaluations/templates/items", headers=auth(member_token)
    )

    assert response.status_code == 403
