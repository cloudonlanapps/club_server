"""Evaluation visibility and authorization (#302, #535, R7, R34-R45)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    answer_of,
    auth,
    create_evaluation_response,
    create_general_evaluation,
    create_template,
    flat_layout,
    get_evaluation,
    item_ids,
    publish,
    put_answer,
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


async def _published_with_private_item(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, str, int, int, int]:
    """A published evaluation of alice with one public and one private question.

    Returns admin, coach and alice tokens, the evaluation id, and the public
    and private item ids. Both questions are answered with a coach note.
    """
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(
        client,
        admin_token,
        layout=[
            rating_item("Skating"),
            {
                "section": "Staff only",
                "items": [qa_item("Private notes", isPrivate=True)],
            },
        ],
    )
    public_id, private_id = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    for item_id, body in (
        (public_id, {"valueNum": 4, "coachNote": "Strong edges"}),
        (private_id, {"valueText": "Talk to the parents"}),
    ):
        written = await put_answer(client, coach_token, evaluation_id, item_id, **body)
        assert written.status_code == 200, written.text
    await publish(client, coach_token, evaluation_id)
    return admin_token, coach_token, alice_token, evaluation_id, public_id, private_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R38")
async def test_should_hide_unpublished_evaluation_from_subject(
    client: AsyncClient, db_session: AsyncSession
):
    """R38: a draft does not exist as far as the member is concerned."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{evaluation_id}", headers=auth(alice_token)
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R16")
@pytest.mark.requirement("evaluation:R38")
async def test_should_show_published_evaluation_to_subject(
    client: AsyncClient, db_session: AsyncSession
):
    """R38: publication is the step that makes it visible, with what it needs to render."""
    _, _, alice_token, evaluation_id, public_id, _ = await _published_with_private_item(
        client, db_session
    )

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{evaluation_id}", headers=auth(alice_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template"]["name"] == "General review"
    assert answer_of(body, public_id)["valueNum"] == 4

    listed = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(alice_token)
    )
    assert listed.status_code == 200
    assert [e["id"] for e in listed.json()["items"]] == [evaluation_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R39")
async def test_should_never_expose_a_private_item_to_the_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R39: a private item — question and answer — has no route to the member."""
    (
        _,
        _,
        alice_token,
        evaluation_id,
        public_id,
        private_id,
    ) = await _published_with_private_item(client, db_session)

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{evaluation_id}", headers=auth(alice_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["id"] for i in body["template"]["items"]] == [public_id]
    assert private_id not in flat_layout(body["template"]["layout"])
    assert [a["itemId"] for a in body["answers"]] == [public_id]
    assert "Talk to the parents" not in response.text
    assert "Private notes" not in response.text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R39")
async def test_should_drop_a_section_left_empty_by_private_items(
    client: AsyncClient, db_session: AsyncSession
):
    """R39: the member's layout loses the private keys, and the sections they emptied."""
    _, _, alice_token, evaluation_id, public_id, _ = await _published_with_private_item(
        client, db_session
    )

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{evaluation_id}", headers=auth(alice_token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["template"]["layout"] == [public_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R39a")
async def test_should_show_the_coach_note_on_a_public_item_to_the_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R39a: the coach note on a public item is written for the member."""
    _, _, alice_token, evaluation_id, public_id, _ = await _published_with_private_item(
        client, db_session
    )

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{evaluation_id}", headers=auth(alice_token)
    )

    assert response.status_code == 200, response.text
    assert answer_of(response.json(), public_id)["coachNote"] == "Strong edges"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R41")
async def test_should_show_the_effective_owner_every_field(
    client: AsyncClient, db_session: AsyncSession
):
    """R41: the effective owner sees private items too, published or not."""
    (
        _,
        coach_token,
        _,
        evaluation_id,
        public_id,
        private_id,
    ) = await _published_with_private_item(client, db_session)

    read = await get_evaluation(client, coach_token, evaluation_id)

    assert answer_of(read, public_id)["coachNote"] == "Strong edges"
    assert answer_of(read, private_id)["valueText"] == "Talk to the parents"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R40")
async def test_should_reject_member_reading_another_members_evaluations(
    client: AsyncClient, db_session: AsyncSession
):
    """R40: a member can only read evaluations about themselves."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    bob_token = await create_member_user(db_session, "bob")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    await publish(client, coach_token, evaluation_id)

    response = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(bob_token)
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R43")
async def test_should_reject_anonymous_callers(client: AsyncClient):
    """R43: evaluations are private in every state."""
    response = await client.get("/v1/evaluations")

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R45")
async def test_should_reject_plain_member_creating_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R45: writing an evaluation needs the coach role."""
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    bob_token = await create_member_user(db_session, "bob")
    template_id = await create_template(client, admin_token)

    response = await create_evaluation_response(client, bob_token, template_id, "alice")

    assert response.status_code == 403
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert listed.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R35")
async def test_should_hide_evaluation_from_coach_acting_on_another_coachs(
    client: AsyncClient, db_session: AsyncSession
):
    """R35: only the effective owner acts; anyone else finds nothing to act on."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    other_token = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(other_token)
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert read["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R34")
async def test_should_create_evaluation_owned_by_the_calling_coach(
    client: AsyncClient, db_session: AsyncSession
):
    """R34: a coach's evaluation is created by and owned by that coach."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice"
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert (read["createdBy"], read["owner"]) == ("coach", None)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R34")
async def test_should_reject_naming_another_owner_on_create(
    client: AsyncClient, db_session: AsyncSession
):
    """R34: there is no way to create an evaluation on another coach's behalf."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", owner="othercoach"
    )

    assert response.status_code == 422
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R34")
@pytest.mark.parametrize("who", ["super_admin", "plain_admin"])
async def test_should_reject_an_admin_creating_an_evaluation(
    client: AsyncClient, db_session: AsyncSession, who: str
):
    """R34 (#535): an admin writes templates and evaluates no one."""
    admin_token = await create_admin_user(db_session)
    plain_admin = await create_regular_admin_user(db_session, "plainadmin")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    token = admin_token if who == "super_admin" else plain_admin

    response = await create_evaluation_response(client, token, template_id, "alice")

    assert response.status_code == 403
    listed = await client.get("/v1/evaluations", headers=auth(token))
    assert listed.status_code == 200
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R38a")
@pytest.mark.parametrize("status_after", ["draft", "saved"])
@pytest.mark.parametrize("reader", ["othercoach", "admin"])
async def test_should_hide_an_unpublished_evaluation_from_everyone_but_its_owner(
    client: AsyncClient, db_session: AsyncSession, status_after: str, reader: str
):
    """R38a: before publication only the effective owner knows it exists."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    other_token = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    if status_after == "saved":
        saved = await client.post(
            f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(coach_token)
        )
        assert saved.status_code == 200, saved.text
    token = other_token if reader == "othercoach" else admin_token

    read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(token)
    )
    listed = await client.get("/v1/evaluations", headers=auth(token))

    assert read.status_code == 404
    assert read.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    owner_read = await get_evaluation(client, coach_token, evaluation_id)
    assert owner_read["status"] == status_after


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R42")
async def test_should_let_another_coach_read_published_evaluation_without_private_items(
    client: AsyncClient, db_session: AsyncSession
):
    """R42: any coach reads the member surface, in the member projection."""
    _, _, _, evaluation_id, public_id, _ = await _published_with_private_item(
        client, db_session
    )
    other_token = await create_coach_user(db_session, "othercoach")

    listed = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(other_token)
    )
    staff_read = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(other_token)
    )

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [evaluation_id]
    assert [a["itemId"] for a in listed.json()["items"][0]["answers"]] == [public_id]
    assert "Talk to the parents" not in listed.text
    assert staff_read.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R42")
async def test_should_show_an_admin_no_evaluations_on_any_surface(
    client: AsyncClient, db_session: AsyncSession
):
    """R42 (#535): an admin sees no evaluations, published ones included."""
    admin_token, _, _, evaluation_id, _, _ = await _published_with_private_item(
        client, db_session
    )
    plain_admin = await create_regular_admin_user(db_session, "plainadmin")

    for token in (admin_token, plain_admin):
        staff_list = await client.get("/v1/evaluations", headers=auth(token))
        staff_read = await client.get(
            f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(token)
        )
        member_list = await client.get(
            "/v1/myevaluations/by_id/alice", headers=auth(token)
        )
        assert staff_list.json()["items"] == []
        assert staff_read.status_code == 404
        assert member_list.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R7")
async def test_should_allow_member_to_hold_several_general_evaluations(
    client: AsyncClient, db_session: AsyncSession
):
    """R7: two coaches may each file a report of one member on one template."""
    admin_token = await create_admin_user(db_session)
    first_token = await create_coach_user(db_session, "coach")
    second_token = await create_coach_user(db_session, "othercoach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)

    first = await create_general_evaluation(client, first_token, template_id, "alice")
    second = await create_general_evaluation(client, second_token, template_id, "alice")
    await publish(client, first_token, first)
    await publish(client, second_token, second)

    listed = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(alice_token)
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
