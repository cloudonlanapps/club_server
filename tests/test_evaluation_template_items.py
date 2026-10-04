"""Template items and layout, edited a piece at a time (#535).

R12, R12a: each item is one variant of a union selected by its type.
R12c: the layout places every item exactly once, one section level deep.
R13a: no server text limits. R26a: piecewise edits. R49: at least a question.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    choice_item,
    create_template,
    create_template_response,
    flat_layout,
    get_template,
    info_item,
    item_ids,
    qa_item,
    rating_item,
)
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


def _base(template_id: int) -> str:
    return f"/v1/evaluations/templates/by_id/{template_id}"


async def _two_items(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int, int, int]:
    """Admin token, a template of a rating and a Q & A, and the two item ids."""
    admin_token = await create_admin_user(db_session)
    template_id = await create_template(
        client, admin_token, layout=[rating_item(), qa_item()]
    )
    rating_id, qa_id = await item_ids(client, admin_token, template_id)
    return admin_token, template_id, rating_id, qa_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26a")
async def test_should_append_an_added_item_to_the_layout(
    client: AsyncClient, db_session: AsyncSession
):
    """R26a: adding an item places it, at the end when no section is named."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)

    response = await client.post(
        f"{_base(template_id)}/items",
        json={"item": choice_item()},
        headers=auth(admin_token),
    )

    assert response.status_code == 201, response.text
    read = await get_template(client, admin_token, template_id)
    new_id = read["items"][-1]["id"]
    assert read["layout"] == [rating_id, qa_id, new_id]
    assert read["items"][-1]["type"] == "singleChoice"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26a")
async def test_should_add_an_item_into_a_named_section(
    client: AsyncClient, db_session: AsyncSession
):
    """R26a: naming a section adds into it, creating it when it is new."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)

    first = await client.post(
        f"{_base(template_id)}/items",
        json={"item": rating_item("Balance"), "section": "Skating"},
        headers=auth(admin_token),
    )
    second = await client.post(
        f"{_base(template_id)}/items",
        json={"item": rating_item("Stops"), "section": "Skating"},
        headers=auth(admin_token),
    )

    assert first.status_code == second.status_code == 201
    read = await get_template(client, admin_token, template_id)
    balance_id, stops_id = [i["id"] for i in read["items"][-2:]]
    assert read["layout"] == [
        rating_id,
        qa_id,
        {"section": "Skating", "items": [balance_id, stops_id]},
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26a")
async def test_should_replace_one_item_and_keep_its_id(
    client: AsyncClient, db_session: AsyncSession
):
    """R26a: rewording a question changes it in place; its id is its identity."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)

    response = await client.put(
        f"{_base(template_id)}/items/{rating_id}",
        json=rating_item("Forward stride", isRequired=True, isPrivate=True),
        headers=auth(admin_token),
    )

    assert response.status_code == 200, response.text
    read = await get_template(client, admin_token, template_id)
    item = read["items"][0]
    assert (item["id"], item["question"], item["isRequired"], item["isPrivate"]) == (
        rating_id,
        "Forward stride",
        True,
        True,
    )
    assert read["layout"] == [rating_id, qa_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26a")
async def test_should_refuse_changing_an_items_type(
    client: AsyncClient, db_session: AsyncSession
):
    """R26a: an item's type is fixed once it is added."""
    admin_token, template_id, rating_id, _ = await _two_items(client, db_session)

    response = await client.put(
        f"{_base(template_id)}/items/{rating_id}",
        json=qa_item("Skating"),
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ITEM_TYPE_FIXED"
    read = await get_template(client, admin_token, template_id)
    assert read["items"][0]["type"] == "rating"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26a")
async def test_should_remove_an_item_and_its_layout_entry(
    client: AsyncClient, db_session: AsyncSession
):
    """R26a: removing an item takes it out of the layout as well."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)

    response = await client.delete(
        f"{_base(template_id)}/items/{qa_id}", headers=auth(admin_token)
    )

    assert response.status_code == 200, response.text
    read = await get_template(client, admin_token, template_id)
    assert read["layout"] == [rating_id]
    assert [i["id"] for i in read["items"]] == [rating_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26a")
async def test_should_answer_404_for_an_item_of_another_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R26a: an item is edited through its own template only."""
    admin_token, template_id, rating_id, _ = await _two_items(client, db_session)
    other_id = await create_template(client, admin_token, name="Other")

    response = await client.delete(
        f"{_base(other_id)}/items/{rating_id}", headers=auth(admin_token)
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ITEM_NOT_FOUND"
    read = await get_template(client, admin_token, template_id)
    assert rating_id in flat_layout(read["layout"])


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12c")
async def test_should_reorder_and_regroup_through_the_layout(
    client: AsyncClient, db_session: AsyncSession
):
    """R12c: a layout naming every item once, in sections one level deep, is taken."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)
    layout = [qa_id, {"section": "Skating", "items": [rating_id]}]

    response = await client.patch(
        _base(template_id), json={"layout": layout}, headers=auth(admin_token)
    )

    assert response.status_code == 200, response.text
    read = await get_template(client, admin_token, template_id)
    assert read["layout"] == layout


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12c")
@pytest.mark.parametrize(
    "case", ["missing", "duplicated", "unknown", "nested", "untitled"]
)
async def test_should_reject_a_layout_that_does_not_place_every_item_once(
    client: AsyncClient, db_session: AsyncSession, case: str
):
    """R12c: every item exactly once, no other ids, sections never nest."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)
    layouts: dict[str, list[Any]] = {
        "missing": [rating_id],
        "duplicated": [rating_id, qa_id, rating_id],
        "unknown": [rating_id, qa_id, 999_999],
        "nested": [
            {"section": "A", "items": [rating_id, {"section": "B", "items": [qa_id]}]}
        ],
        "untitled": [{"section": "", "items": [rating_id, qa_id]}],
    }

    response = await client.patch(
        _base(template_id),
        json={"layout": layouts[case]},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    read = await get_template(client, admin_token, template_id)
    assert read["layout"] == [rating_id, qa_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12c")
async def test_should_reject_nested_sections_on_create(
    client: AsyncClient, db_session: AsyncSession
):
    """R12c: a section holds items only, on create as on edit."""
    admin_token = await create_admin_user(db_session)
    # Commit the admin: the refused request's rollback would take it along.
    await db_session.commit()

    response = await create_template_response(
        client,
        admin_token,
        layout=[
            {
                "section": "Outer",
                "items": [{"section": "Inner", "items": [rating_item()]}],
            }
        ],
    )

    assert response.status_code == 422
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert listed.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49")
@pytest.mark.parametrize("layout", [[], [info_item()]], ids=["empty", "info_only"])
async def test_should_reject_a_template_with_no_question(
    client: AsyncClient, db_session: AsyncSession, layout: list[Any]
):
    """R49: a template that asks nothing cannot evaluate anything."""
    admin_token = await create_admin_user(db_session)
    # Commit the admin: the refused request's rollback would take it along.
    await db_session.commit()

    response = await create_template_response(client, admin_token, layout=layout)

    assert response.status_code == 422
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert listed.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12")
async def test_should_reject_a_property_the_items_type_does_not_allow(
    client: AsyncClient, db_session: AsyncSession
):
    """R12: an item carries its type's properties and no others."""
    admin_token = await create_admin_user(db_session)
    # Commit the admin: the refused request's rollback would take it along.
    await db_session.commit()

    response = await create_template_response(
        client, admin_token, layout=[qa_item(choices=[{"value": "a", "text": "A"}])]
    )

    assert response.status_code == 422
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert listed.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12")
async def test_should_reject_an_unknown_item_type(
    client: AsyncClient, db_session: AsyncSession
):
    """R12: the type selects a known variant or the item is refused."""
    admin_token = await create_admin_user(db_session)
    # Commit the admin: the refused request's rollback would take it along.
    await db_session.commit()

    response = await create_template_response(
        client,
        admin_token,
        layout=[rating_item(), {"type": "slider", "question": "Speed"}],
    )

    assert response.status_code == 422
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert listed.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R12a")
async def test_should_reject_an_inconsistent_item_when_added(
    client: AsyncClient, db_session: AsyncSession
):
    """R12a: a rating's scale must be ordered, on an added item as on create."""
    admin_token, template_id, rating_id, qa_id = await _two_items(client, db_session)

    response = await client.post(
        f"{_base(template_id)}/items",
        json={"item": rating_item("Backwards", rateMin=5, rateMax=1)},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    read = await get_template(client, admin_token, template_id)
    assert read["layout"] == [rating_id, qa_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R13a")
async def test_should_accept_long_text_without_a_server_cap(
    client: AsyncClient, db_session: AsyncSession
):
    """R13a: names, questions and info text have no server length limit."""
    admin_token = await create_admin_user(db_session)
    long_text = "x" * 20_000

    response = await create_template_response(
        client,
        admin_token,
        name=long_text,
        layout=[info_item(long_text), rating_item(long_text)],
    )

    assert response.status_code == 201, response.text
    read = await get_template(client, admin_token, response.json()["id"])
    assert read["name"] == long_text
    assert read["items"][1]["question"] == long_text
