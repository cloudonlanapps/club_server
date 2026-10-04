"""Who writes templates, and their unique names (#535, R46, R49a)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    create_template_response,
    get_template,
    item_ids,
    qa_item,
    rating_item,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")

TEMPLATES = "/v1/evaluations/templates"


def _by_id(template_id: int) -> str:
    return f"{TEMPLATES}/by_id/{template_id}"


async def _staff(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str]:
    """An admin and a coach token, committed."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    await db_session.commit()
    return admin_token, coach_token


async def _names(client: AsyncClient, token: str, path: str = TEMPLATES) -> list[str]:
    listed = await client.get(path, headers=auth(token))
    assert listed.status_code == 200, listed.text
    return [t["name"] for t in listed.json()["items"]]


# R46 — any staff member writes templates ------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_let_a_coach_create_and_rename_a_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: a coach creates a template, and is recorded as its creator."""
    admin_token, coach_token = await _staff(client, db_session)

    template_id = await create_template(client, coach_token, name="Coach drills")
    renamed = await client.patch(
        _by_id(template_id), json={"name": "Edge drills"}, headers=auth(coach_token)
    )

    assert renamed.status_code == 200, renamed.text
    read = await get_template(client, admin_token, template_id)
    assert (read["name"], read["createdBy"]) == ("Edge drills", "coach")


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_let_a_coach_edit_the_items_and_layout_of_any_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: any staff member edits any template, whoever created it."""
    admin_token, coach_token = await _staff(client, db_session)
    template_id = await create_template(client, admin_token)
    [skating] = await item_ids(client, admin_token, template_id)

    added = await client.post(
        f"{_by_id(template_id)}/items",
        json={"item": qa_item("Comments")},
        headers=auth(coach_token),
    )
    assert added.status_code == 201, added.text
    [_, comments] = await item_ids(client, admin_token, template_id)
    replaced = await client.put(
        f"{_by_id(template_id)}/items/{skating}",
        json=rating_item("Stride"),
        headers=auth(coach_token),
    )
    relaid = await client.patch(
        _by_id(template_id),
        json={"layout": [comments, skating]},
        headers=auth(coach_token),
    )
    removed = await client.delete(
        f"{_by_id(template_id)}/items/{comments}", headers=auth(coach_token)
    )

    assert (replaced.status_code, relaid.status_code, removed.status_code) == (
        200,
        200,
        200,
    )
    read = await get_template(client, admin_token, template_id)
    assert [i["question"] for i in read["items"]] == ["Stride"]
    assert read["layout"] == [skating]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_let_a_coach_delete_list_and_restore_templates(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: soft delete, the deleted listing and restore are a coach's too."""
    admin_token, coach_token = await _staff(client, db_session)
    template_id = await create_template(client, admin_token, name="Old review")

    deleted = await client.delete(_by_id(template_id), headers=auth(coach_token))
    assert deleted.status_code == 200, deleted.text
    assert await _names(client, coach_token, f"{TEMPLATES}/deleted") == ["Old review"]
    assert await _names(client, coach_token) == []

    restored = await client.post(
        f"{_by_id(template_id)}/restore", headers=auth(coach_token)
    )

    assert restored.status_code == 200, restored.text
    assert await _names(client, admin_token) == ["Old review"]
    assert await _names(client, coach_token, f"{TEMPLATES}/deleted") == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_refuse_every_template_write_to_a_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: templates are staff business; a member gets 403 for each write."""
    admin_token, _ = await _staff(client, db_session)
    member_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    [item_id] = await item_ids(client, admin_token, template_id)
    base = _by_id(template_id)
    headers = auth(member_token)

    responses = [
        await create_template_response(client, member_token, name="Mine"),
        await client.patch(base, json={"name": "Mine"}, headers=headers),
        await client.post(f"{base}/items", json={"item": qa_item()}, headers=headers),
        await client.put(f"{base}/items/{item_id}", json=qa_item(), headers=headers),
        await client.delete(f"{base}/items/{item_id}", headers=headers),
        await client.delete(base, headers=headers),
        await client.post(f"{base}/restore", headers=headers),
        await client.get(f"{TEMPLATES}/deleted", headers=headers),
    ]

    assert [r.status_code for r in responses] == [403] * len(responses)
    read = await get_template(client, admin_token, template_id)
    assert (read["name"], read["deletedAtUtc"]) == ("General review", None)
    assert [i["question"] for i in read["items"]] == ["Skating"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
@pytest.mark.requirement("evaluation:R27")
async def test_should_freeze_a_used_template_for_a_coach_too(
    client: AsyncClient, db_session: AsyncSession
):
    """R46, R27: the in-use freeze applies to every writer alike."""
    admin_token, coach_token = await _staff(client, db_session)
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, coach_token)
    _ = await create_general_evaluation(client, coach_token, template_id, "alice")

    response = await client.post(
        f"{_by_id(template_id)}/items",
        json={"item": qa_item()},
        headers=auth(coach_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TEMPLATE_IN_USE"
    assert len(await item_ids(client, admin_token, template_id)) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
@pytest.mark.requirement("evaluation:R47")
async def test_should_refuse_hard_delete_of_a_template_to_a_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R46, R47: hard deletion stays the super admin's."""
    admin_token, coach_token = await _staff(client, db_session)
    template_id = await create_template(client, coach_token)
    deleted = await client.delete(_by_id(template_id), headers=auth(coach_token))
    assert deleted.status_code == 200, deleted.text

    response = await client.delete(
        f"{_by_id(template_id)}/hard", headers=auth(coach_token)
    )

    assert response.status_code == 403
    assert await _names(client, admin_token, f"{TEMPLATES}/deleted") == [
        "General review"
    ]


# R49a — a live template's name is unique ------------------------------------


def _name_taken(response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "TEMPLATE_NAME_TAKEN"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49a")
@pytest.mark.parametrize(
    "name", ["General review", "  general REVIEW  "], ids=["same", "case_and_space"]
)
async def test_should_refuse_creating_a_template_whose_name_is_taken(
    client: AsyncClient, db_session: AsyncSession, name: str
):
    """R49a: names compare without regard to case or surrounding whitespace."""
    admin_token, coach_token = await _staff(client, db_session)
    _ = await create_template(client, admin_token)

    response = await create_template_response(client, coach_token, name=name)

    _name_taken(response)
    assert await _names(client, admin_token) == ["General review"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49a")
async def test_should_refuse_renaming_a_template_to_a_taken_name(
    client: AsyncClient, db_session: AsyncSession
):
    """R49a: a rename may not take another live template's name."""
    admin_token, _ = await _staff(client, db_session)
    _ = await create_template(client, admin_token, name="Camp report")
    other = await create_template(client, admin_token, name="Term report")

    response = await client.patch(
        _by_id(other), json={"name": " camp REPORT"}, headers=auth(admin_token)
    )

    _name_taken(response)
    assert (await get_template(client, admin_token, other))["name"] == "Term report"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49a")
async def test_should_allow_renaming_a_template_to_its_own_name_in_another_case(
    client: AsyncClient, db_session: AsyncSession
):
    """R49a: a template does not collide with itself."""
    admin_token, _ = await _staff(client, db_session)
    template_id = await create_template(client, admin_token, name="Camp report")

    response = await client.patch(
        _by_id(template_id), json={"name": "Camp Report"}, headers=auth(admin_token)
    )

    assert response.status_code == 200, response.text
    assert (await get_template(client, admin_token, template_id))["name"] == (
        "Camp Report"
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49a")
async def test_should_free_the_name_of_a_deleted_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R49a: a soft-deleted template holds no name."""
    admin_token, _ = await _staff(client, db_session)
    first = await create_template(client, admin_token, name="Camp report")
    deleted = await client.delete(_by_id(first), headers=auth(admin_token))
    assert deleted.status_code == 200, deleted.text

    second = await create_template_response(client, admin_token, name="Camp report")

    assert second.status_code == 201, second.text
    assert await _names(client, admin_token) == ["Camp report"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R49a")
async def test_should_refuse_restoring_a_template_whose_name_was_taken(
    client: AsyncClient, db_session: AsyncSession
):
    """R49a: a restore may not bring back a second template of one name."""
    admin_token, _ = await _staff(client, db_session)
    first = await create_template(client, admin_token, name="Camp report")
    deleted = await client.delete(_by_id(first), headers=auth(admin_token))
    assert deleted.status_code == 200, deleted.text
    _ = await create_template(client, admin_token, name="camp report")

    response = await client.post(f"{_by_id(first)}/restore", headers=auth(admin_token))

    _name_taken(response)
    assert await _names(client, admin_token, f"{TEMPLATES}/deleted") == ["Camp report"]
    assert await _names(client, admin_token) == ["camp report"]
