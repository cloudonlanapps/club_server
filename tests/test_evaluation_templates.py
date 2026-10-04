"""Evaluation templates: access, soft delete, and the in-use guard (#302, #535)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    create_template_response,
    get_evaluation,
    get_template,
    item_ids,
    qa_item,
    rating_item,
)
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _used_template(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int, int]:
    """Admin and coach tokens, a template, its item, and an evaluation using it."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    [item_id] = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    return admin_token, coach_token, template_id, item_id, evaluation_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_reject_member_creating_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: templates are writable only by staff — an admin or a coach."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, "alice")
    # Commit the users: the refused request's rollback would take them along.
    await db_session.commit()

    response = await create_template_response(client, member_token, name="Sneaky")

    assert response.status_code == 403
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert listed.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_reject_member_editing_template_items(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: adding, changing or removing an item is a template write, staff only."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    [item_id] = await item_ids(client, admin_token, template_id)
    base = f"/v1/evaluations/templates/by_id/{template_id}"

    added = await client.post(
        f"{base}/items", json={"item": qa_item()}, headers=auth(member_token)
    )
    replaced = await client.put(
        f"{base}/items/{item_id}",
        json=rating_item("Renamed"),
        headers=auth(member_token),
    )
    removed = await client.delete(f"{base}/items/{item_id}", headers=auth(member_token))

    assert (added.status_code, replaced.status_code, removed.status_code) == (
        403,
        403,
        403,
    )
    read = await get_template(client, admin_token, template_id)
    assert [i["question"] for i in read["items"]] == ["Skating"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R46")
async def test_should_allow_coach_reading_templates(
    client: AsyncClient, db_session: AsyncSession
):
    """R46: reading is open to any staff member, since naming one is a read."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    template_id = await create_template(client, admin_token)

    response = await client.get("/v1/evaluations/templates", headers=auth(coach_token))

    assert response.status_code == 200
    assert [t["id"] for t in response.json()["items"]] == [template_id]
    one = await get_template(client, coach_token, template_id)
    assert one["items"][0]["question"] == "Skating"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R27")
async def test_should_reject_template_deletion_while_referenced(
    client: AsyncClient, db_session: AsyncSession
):
    """R27: an evaluation's answers make its template a live reference."""
    admin_token, _, template_id, _, _ = await _used_template(client, db_session)

    response = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}", headers=auth(admin_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TEMPLATE_IN_USE"
    still_there = await get_template(client, admin_token, template_id)
    assert still_there["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26")
async def test_should_soft_delete_and_restore_unreferenced_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R26: templates follow the same soft-delete lifecycle as every other entity."""
    admin_token = await create_admin_user(db_session)
    template_id = await create_template(client, admin_token)

    deleted = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}", headers=auth(admin_token)
    )
    assert deleted.status_code == 200
    assert deleted.json()["deletedAtUtc"] is not None

    gone = await client.get(
        f"/v1/evaluations/templates/by_id/{template_id}", headers=auth(admin_token)
    )
    assert gone.status_code == 404

    listed = await client.get(
        "/v1/evaluations/templates/deleted", headers=auth(admin_token)
    )
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()["items"]] == [template_id]

    restored = await client.post(
        f"/v1/evaluations/templates/by_id/{template_id}/restore",
        headers=auth(admin_token),
    )
    assert restored.status_code == 200
    assert restored.json()["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R27")
@pytest.mark.parametrize("change", ["add", "replace", "remove", "layout"])
async def test_should_reject_item_or_layout_change_while_template_referenced(
    client: AsyncClient, db_session: AsyncSession, change: str
):
    """R27: changing the contract under existing evaluations is refused."""
    admin_token, _, template_id, item_id, _ = await _used_template(client, db_session)
    base = f"/v1/evaluations/templates/by_id/{template_id}"
    requests = {
        "add": ("POST", f"{base}/items", {"item": qa_item()}),
        "replace": ("PUT", f"{base}/items/{item_id}", rating_item("Edges")),
        "remove": ("DELETE", f"{base}/items/{item_id}", None),
        "layout": ("PATCH", base, {"layout": [{"section": "All", "items": [item_id]}]}),
    }
    method, url, body = requests[change]

    response = await client.request(method, url, json=body, headers=auth(admin_token))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TEMPLATE_IN_USE"
    read = await get_template(client, admin_token, template_id)
    assert read["layout"] == [item_id]
    assert [i["question"] for i in read["items"]] == ["Skating"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R27")
async def test_should_allow_renaming_a_template_in_use(
    client: AsyncClient, db_session: AsyncSession
):
    """R27 (#535): the name is not part of the contract, so renaming stays allowed."""
    admin_token, _, template_id, _, _ = await _used_template(client, db_session)

    response = await client.patch(
        f"/v1/evaluations/templates/by_id/{template_id}",
        json={"name": "Spring review"},
        headers=auth(admin_token),
    )

    assert response.status_code == 200, response.text
    read = await get_template(client, admin_token, template_id)
    assert read["name"] == "Spring review"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R27")
async def test_should_reject_item_change_while_soft_deleted_evaluation_references_template(
    client: AsyncClient, db_session: AsyncSession
):
    """#486: a soft-deleted evaluation can be restored, so its answers still hold the contract."""
    (
        admin_token,
        coach_token,
        template_id,
        item_id,
        evaluation_id,
    ) = await _used_template(client, db_session)
    written = await client.put(
        f"/v1/evaluations/by_id/{evaluation_id}/answers/{item_id}",
        json={"valueNum": 4},
        headers=auth(coach_token),
    )
    assert written.status_code == 200, written.text
    deleted = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert deleted.status_code == 200

    response = await client.put(
        f"/v1/evaluations/templates/by_id/{template_id}/items/{item_id}",
        json=rating_item("Skating", rateMin=1, rateMax=10),
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TEMPLATE_IN_USE"
    restored = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/restore", headers=auth(coach_token)
    )
    assert restored.status_code == 200
    assert [(a["itemId"], a["valueNum"]) for a in restored.json()["answers"]] == [
        (item_id, 4)
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R47")
async def test_should_reject_hard_delete_of_template_by_non_super_admin(
    client: AsyncClient, db_session: AsyncSession
):
    """R47: hard deletion is super-admin only."""
    admin_token = await create_admin_user(db_session)
    plain_admin = await create_regular_admin_user(db_session, username="plainadmin")
    template_id = await create_template(client, admin_token)

    response = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}/hard",
        headers=auth(plain_admin),
    )

    assert response.status_code == 403
    still_there = await get_template(client, admin_token, template_id)
    assert still_there["id"] == template_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26")
async def test_should_refuse_hard_delete_of_live_template(
    client: AsyncClient, db_session: AsyncSession
):
    """#487: hard delete follows a soft delete, as for every other entity (R26)."""
    admin_token = await create_admin_user(db_session)
    template_id = await create_template(client, admin_token)

    response = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}/hard",
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HARD_DELETE_NEEDS_SOFT_DELETE"
    still_there = await get_template(client, admin_token, template_id)
    assert still_there["id"] == template_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R26")
async def test_should_hard_delete_template_after_soft_delete(
    client: AsyncClient, db_session: AsyncSession
):
    """#487: soft then hard delete still removes a template for good."""
    admin_token = await create_admin_user(db_session)
    template_id = await create_template(client, admin_token)
    soft = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}", headers=auth(admin_token)
    )
    assert soft.status_code == 200

    response = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}/hard",
        headers=auth(admin_token),
    )

    assert response.status_code == 204
    listed = await client.get(
        "/v1/evaluations/templates/deleted", headers=auth(admin_token)
    )
    assert listed.json()["items"] == []
    gone = await client.post(
        f"/v1/evaluations/templates/by_id/{template_id}/restore",
        headers=auth(admin_token),
    )
    assert gone.status_code == 404


async def _hard_delete_user(client: AsyncClient, admin: str, username: str) -> None:
    """Soft then hard delete a user, as the super admin."""
    soft = await client.delete(f"/v1/users/by_id/{username}", headers=auth(admin))
    assert soft.status_code == 200, soft.text
    hard = await client.delete(f"/v1/users/by_id/{username}/hard", headers=auth(admin))
    assert hard.status_code == 204, hard.text


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
async def test_should_pass_template_in_use_to_deleting_super_admin_when_creator_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """#490: a template is club material, and other coaches' evaluations use it."""
    admin_token = await create_admin_user(db_session)
    ann_token = await create_regular_admin_user(db_session, username="ann")
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, ann_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    await db_session.commit()

    await _hard_delete_user(client, admin_token, "ann")

    template = await get_template(client, admin_token, template_id)
    assert template["createdBy"] == "admin"
    evaluation = await get_evaluation(client, coach_token, evaluation_id)
    assert evaluation["templateId"] == template_id


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
async def test_should_pass_unused_template_to_deleting_super_admin_when_creator_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """#490: an unused template does not silently disappear with its creator."""
    admin_token = await create_admin_user(db_session)
    ann_token = await create_regular_admin_user(db_session, username="ann")
    template_id = await create_template(client, ann_token)
    await db_session.commit()

    await _hard_delete_user(client, admin_token, "ann")

    template = await get_template(client, admin_token, template_id)
    assert template["createdBy"] == "admin"


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
async def test_should_pass_soft_deleted_template_to_deleting_super_admin_when_creator_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """#490: a soft-deleted template survives too, and can still be restored."""
    admin_token = await create_admin_user(db_session)
    ann_token = await create_regular_admin_user(db_session, username="ann")
    template_id = await create_template(client, ann_token)
    soft = await client.delete(
        f"/v1/evaluations/templates/by_id/{template_id}", headers=auth(ann_token)
    )
    assert soft.status_code == 200

    await _hard_delete_user(client, admin_token, "ann")

    restored = await client.post(
        f"/v1/evaluations/templates/by_id/{template_id}/restore",
        headers=auth(admin_token),
    )
    assert restored.status_code == 200
    assert restored.json()["createdBy"] == "admin"
    assert restored.json()["deletedAtUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R27a")
async def test_should_say_whether_a_template_is_in_use(
    client: AsyncClient, db_session: AsyncSession
):
    """R27a: reads say in use once any evaluation, soft-deleted included, uses it."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    unused = await get_template(client, admin_token, template_id)
    assert unused["inUse"] is False

    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    deleted = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert deleted.status_code == 200, deleted.text

    one = await get_template(client, coach_token, template_id)
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert one["inUse"] is True
    assert [t["inUse"] for t in listed.json()["items"]] == [True]
