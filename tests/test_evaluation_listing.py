"""Evaluation listings, filters and paging (#302, #535, R42, R57-R60)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    past_period,
    publish,
    transfer,
)
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _three_by_coach(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, list[int]]:
    """Admin and coach tokens; coach's evaluations of alice, alice and bob.

    alice's second covers a period, so it is not her first's twin (R7).
    """
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    _ = await create_member_user(db_session, "bob")
    template_id = await create_template(client, admin_token)
    ids = [
        await create_general_evaluation(
            client, coach_token, template_id, member, **extra
        )
        for member, extra in (("alice", {}), ("alice", past_period(30, 1)), ("bob", {}))
    ]
    return admin_token, coach_token, ids


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R60")
@pytest.mark.requirement("evaluation:R57")
async def test_should_count_only_the_filtered_set_when_paging(
    client: AsyncClient, db_session: AsyncSession
):
    """R60: the archive counted every evaluation in the system regardless of filters.

    This is the regression test for that defect. With three evaluations
    across two members, a filtered listing must report the filtered total,
    not three.
    """
    _, coach_token, _ = await _three_by_coach(client, db_session)

    unfiltered = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert unfiltered.json()["total"] == 3

    filtered = await client.get(
        "/v1/evaluations?createdFor=bob", headers=auth(coach_token)
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert len(filtered.json()["items"]) == 1
    assert filtered.json()["items"][0]["createdFor"] == "bob"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R60")
async def test_should_count_filtered_set_when_limit_hides_the_difference(
    client: AsyncClient, db_session: AsyncSession
):
    """R60: paging must not mask a wrong total."""
    _, coach_token, _ = await _three_by_coach(client, db_session)

    response = await client.get(
        "/v1/evaluations?createdFor=alice&limit=1", headers=auth(coach_token)
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R42")
async def test_should_show_coach_only_their_own_evaluations(
    client: AsyncClient, db_session: AsyncSession
):
    """R42: a coach sees the evaluations they own."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    other_token = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    mine = await create_general_evaluation(client, coach_token, template_id, "alice")
    _ = await create_general_evaluation(client, other_token, template_id, "alice")

    response = await client.get("/v1/evaluations", headers=auth(coach_token))

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [e["id"] for e in response.json()["items"]] == [mine]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R42")
@pytest.mark.requirement("evaluation:R57")
async def test_should_move_an_evaluation_between_listings_on_transfer(
    client: AsyncClient, db_session: AsyncSession
):
    """R42, R57 (#535): listings follow the effective owner, not the creator."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    other_token = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    moved = await transfer(client, coach_token, evaluation_id, "othercoach")
    assert moved.status_code == 204, moved.text

    mine = await client.get("/v1/evaluations", headers=auth(coach_token))
    theirs = await client.get("/v1/evaluations", headers=auth(other_token))
    assert mine.json()["items"] == []
    assert [e["id"] for e in theirs.json()["items"]] == [evaluation_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R42")
async def test_should_show_admin_no_evaluations(
    client: AsyncClient, db_session: AsyncSession
):
    """R42 (#535): an admin sees none of them on the staff surface."""
    admin_token, _, _ = await _three_by_coach(client, db_session)
    plain_admin = await create_regular_admin_user(db_session, "plainadmin")

    for token in (admin_token, plain_admin):
        response = await client.get("/v1/evaluations", headers=auth(token))
        deleted = await client.get("/v1/evaluations/deleted", headers=auth(token))
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert deleted.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R57")
async def test_should_filter_by_status(client: AsyncClient, db_session: AsyncSession):
    """R57: listings can be narrowed to a lifecycle state."""
    _, coach_token, ids = await _three_by_coach(client, db_session)
    await publish(client, coach_token, ids[0])

    response = await client.get(
        "/v1/evaluations?status=published", headers=auth(coach_token)
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == ids[0]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R25")
async def test_should_exclude_soft_deleted_from_the_live_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """R25: a soft-deleted evaluation leaves the live listing."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    kept = await create_general_evaluation(client, coach_token, template_id, "alice")
    removed = await create_general_evaluation(
        client, coach_token, template_id, "alice", **past_period(30, 1)
    )
    gone = await client.delete(
        f"/v1/evaluations/by_id/{removed}", headers=auth(coach_token)
    )
    assert gone.status_code == 200, gone.text

    live = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert live.json()["total"] == 1
    assert [e["id"] for e in live.json()["items"]] == [kept]

    deleted = await client.get("/v1/evaluations/deleted", headers=auth(coach_token))
    assert deleted.json()["total"] == 1
    assert [e["id"] for e in deleted.json()["items"]] == [removed]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R59")
async def test_should_order_member_listing_by_most_recently_published(
    client: AsyncClient, db_session: AsyncSession
):
    """R59: a member's own listing is most recently published first."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    first = await create_general_evaluation(client, coach_token, template_id, "alice")
    second = await create_general_evaluation(
        client, coach_token, template_id, "alice", **past_period(30, 1)
    )
    await publish(client, coach_token, first)
    await publish(client, coach_token, second)

    response = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(alice_token)
    )

    assert response.status_code == 200
    assert [e["id"] for e in response.json()["items"]] == [second, first]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R38")
async def test_should_hide_unpublished_from_member_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """R38: only published evaluations reach the member's listing."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    published_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    _ = await create_general_evaluation(
        client, coach_token, template_id, "alice", **past_period(30, 1)
    )
    await publish(client, coach_token, published_id)

    response = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(alice_token)
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == published_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R17")
async def test_should_drop_withdrawn_evaluation_from_member_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """R17: withdrawal removes it from the member's view again."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    await publish(client, coach_token, evaluation_id)
    before = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(alice_token)
    )
    assert before.json()["total"] == 1

    withdrawn = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/unpublish", headers=auth(coach_token)
    )
    assert withdrawn.status_code == 200, withdrawn.text

    after = await client.get("/v1/myevaluations/by_id/alice", headers=auth(alice_token))
    assert after.status_code == 200
    assert after.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R42")
async def test_should_let_a_coach_read_a_members_listing(
    client: AsyncClient, db_session: AsyncSession
):
    """R42: any coach can see a member's published evaluations on the member surface."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    other_token = await create_coach_user(db_session, "othercoach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    await publish(client, coach_token, evaluation_id)

    response = await client.get(
        "/v1/myevaluations/by_id/alice", headers=auth(other_token)
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R47")
async def test_should_reject_plain_admin_hard_deleting_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R47: hard deletion of an evaluation is super-admin only."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    plain_admin = await create_regular_admin_user(db_session, username="plainadmin")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    soft = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert soft.status_code == 200, soft.text

    response = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}/hard", headers=auth(plain_admin)
    )

    assert response.status_code == 403
    still_there = await client.get("/v1/evaluations/deleted", headers=auth(coach_token))
    assert [e["id"] for e in still_there.json()["items"]] == [evaluation_id]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R25")
async def test_should_refuse_hard_delete_of_live_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """#487: hard delete follows a soft delete, as for every other entity (R25)."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}/hard", headers=auth(admin_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HARD_DELETE_NEEDS_SOFT_DELETE"
    still_there = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert still_there.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R25")
async def test_should_hard_delete_evaluation_after_soft_delete(
    client: AsyncClient, db_session: AsyncSession
):
    """#487: soft then hard delete still removes an evaluation for good."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    soft = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach_token)
    )
    assert soft.status_code == 200

    response = await client.delete(
        f"/v1/evaluations/by_id/{evaluation_id}/hard", headers=auth(admin_token)
    )

    assert response.status_code == 204
    listed = await client.get("/v1/evaluations/deleted", headers=auth(coach_token))
    assert listed.json()["items"] == []
    gone = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/restore", headers=auth(coach_token)
    )
    assert gone.status_code == 404
