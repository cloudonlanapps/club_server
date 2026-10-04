"""Evidence for evaluation rules no earlier test proved in full (#402, #535)."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.utils import now_utc_ms

from .evaluation_helpers import (
    answer_of,
    auth,
    create_evaluation_response,
    create_general_evaluation,
    create_template,
    create_template_response,
    create_venue,
    get_evaluation,
    get_template,
    item_ids,
    mark_attendance,
    past_period,
    put_answer,
    rating_item,
)
from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _staff_and_subject(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int]:
    """Admin token, coach token, and the default template; `alice` exists."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    await db_session.commit()
    return admin_token, coach_token, template_id


@pytest.mark.requirement("evaluation:R10a")
@pytest.mark.asyncio
async def test_should_reject_evaluation_created_without_a_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R10a: the template reference is required, not optional."""
    _, coach_token, _ = await _staff_and_subject(client, db_session)

    response = await client.post(
        "/v1/evaluations",
        json={"createdFor": "alice"},
        headers=auth(coach_token),
    )

    assert response.status_code == 422, response.text
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 0


@pytest.mark.requirement("evaluation:R8")
@pytest.mark.requirement("evaluation:R9")
@pytest.mark.asyncio
async def test_should_record_member_creator_status_times_and_answers(
    client: AsyncClient, db_session: AsyncSession
):
    """R8, R9: who it is for, who made it, a status, times; content is answers."""
    admin_token, coach_token, template_id = await _staff_and_subject(client, db_session)
    [item_id] = await item_ids(client, admin_token, template_id)
    before = now_utc_ms()
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    written = await put_answer(
        client,
        coach_token,
        evaluation_id,
        item_id,
        valueNum=4,
        coachNote="Strong edges",
    )
    assert written.status_code == 200, written.text

    body = await get_evaluation(client, coach_token, evaluation_id)
    assert body["createdFor"] == "alice"
    assert body["createdBy"] == "coach"
    assert body["owner"] is None
    assert body["templateId"] == template_id
    assert body["status"] == "draft"
    assert body["createdAtUtc"] >= before
    assert body["publishedAtUtc"] is None
    answer = answer_of(body, item_id)
    assert answer is not None
    assert (answer["valueNum"], answer["coachNote"]) == (4, "Strong edges")
    assert answer["evidence"] == []


async def _event_of_type(
    client: AsyncClient, admin_token: str, type_: str
) -> tuple[int, int]:
    """A one-off or camp coached by `coach`, starting ten minutes from now."""
    venue_id = await create_venue(client, admin_token)
    start = (now_utc_ms() + 10 * 60 * 1000) // 1000 * 1000
    body: dict[str, object] = {
        "title": f"A {type_}",
        "type": type_,
        "venueId": venue_id,
        "startTimeUtc": start,
        "endTimeUtc": start + 2 * 3600 * 1000,
        "coachNames": ["coach"],
    }
    if type_ == "camp":
        body["rrule"] = "FREQ=DAILY;COUNT=3"
    response = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert response.status_code == 201, response.text
    return response.json()["id"], start


@pytest.mark.requirement("evaluation:R3")
@pytest.mark.parametrize("event_type", ["oneOff", "camp"])
@pytest.mark.asyncio
async def test_should_accept_event_scope_for_one_off_and_camp(
    client: AsyncClient, db_session: AsyncSession, event_type: str
):
    """R3: the event may be a one-off or a camp, not only a programme."""
    admin_token, coach_token, template_id = await _staff_and_subject(client, db_session)
    event_id, occurrence = await _event_of_type(client, admin_token, event_type)
    await mark_attendance(client, admin_token, event_id, occurrence, "alice")

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert (read["eventId"], read["periodStartUtc"], read["periodEndUtc"]) == (
        event_id,
        None,
        None,
    )


@pytest.mark.requirement("evaluation:R49")
@pytest.mark.asyncio
async def test_should_record_template_shape_and_creator(
    client: AsyncClient, db_session: AsyncSession
):
    """R49: a name, items, their layout, and who created it — in one write."""
    admin_token = await create_admin_user(db_session)

    created = await create_template_response(
        client,
        admin_token,
        name="Camp report",
        layout=[
            {"type": "info", "markdown": "End-of-camp review"},
            {"section": "Skating", "items": [rating_item("Forward stride")]},
        ],
    )

    assert created.status_code == 201, created.text
    body = await get_template(client, admin_token, created.json()["id"])
    info_id, rating_id = [i["id"] for i in body["items"]]
    assert body["name"] == "Camp report"
    assert body["createdBy"] == "admin"
    assert body["layout"] == [
        info_id,
        {"section": "Skating", "items": [rating_id]},
    ]
    assert [(i["type"], i.get("question")) for i in body["items"]] == [
        ("info", None),
        ("rating", "Forward stride"),
    ]
    assert "scopes" not in body
    assert "description" not in body


@pytest.mark.requirement("evaluation:R50")
@pytest.mark.asyncio
async def test_should_start_a_new_evaluation_as_an_empty_draft_by_the_caller(
    client: AsyncClient, db_session: AsyncSession
):
    """R50: creating names template and member, and starts with no answers."""
    _, coach_token, template_id = await _staff_and_subject(client, db_session)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice"
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert (read["createdBy"], read["createdFor"], read["status"]) == (
        "coach",
        "alice",
        "draft",
    )
    assert read["answers"] == []


@pytest.mark.requirement("evaluation:R50")
@pytest.mark.asyncio
async def test_should_no_longer_offer_applying_a_template(
    client: AsyncClient, db_session: AsyncSession
):
    """R50 (#535): applying a template is gone; creating names the template."""
    _, coach_token, template_id = await _staff_and_subject(client, db_session)

    response = await client.post(
        f"/v1/evaluations/templates/by_id/{template_id}/apply",
        json={"createdFor": "alice"},
        headers=auth(coach_token),
    )

    assert response.status_code in (404, 405)
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 0


@pytest.mark.requirement("evaluation:R52")
@pytest.mark.asyncio
async def test_should_audit_each_traced_move_with_actor_and_ip(
    client: AsyncClient, db_session: AsyncSession
):
    """R52: each traced move names its actor and the IP it came from."""
    admin_token, coach_token, template_id = await _staff_and_subject(client, db_session)
    _ = await create_coach_user(db_session, "othercoach")
    await db_session.commit()
    ip = {"X-Forwarded-For": "9.9.9.9"}
    created = await client.post(
        "/v1/evaluations",
        json={"templateId": template_id, "createdFor": "alice"},
        headers=auth(coach_token) | ip,
    )
    assert created.status_code == 201, created.text
    base = f"/v1/evaluations/by_id/{created.json()['id']}"
    steps = [
        ("POST", "/save", coach_token, None, 200),
        ("POST", "/publish", coach_token, None, 200),
        ("POST", "/unpublish", coach_token, None, 200),
        ("POST", "/revert", coach_token, None, 200),
        ("POST", "/transfer", admin_token, {"owner": "othercoach"}, 204),
    ]
    for method, path, token, body, expected in steps:
        response = await client.request(
            method, base + path, json=body, headers=auth(token) | ip
        )
        assert response.status_code == expected, (method, path, response.text)

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.resource_id == str(created.json()["id"]))
    )
    trace = {
        (row.action, row.actor_username, json.loads(row.details)["ip_address"])
        for row in result.scalars().all()
        if row.resource_type == "evaluation"
    }
    assert trace == {
        ("save_evaluation", "coach", "9.9.9.9"),
        ("publish_evaluation", "coach", "9.9.9.9"),
        ("unpublish_evaluation", "coach", "9.9.9.9"),
        ("revert_evaluation", "coach", "9.9.9.9"),
        ("transfer_evaluation", "admin", "9.9.9.9"),
    }


@pytest.mark.requirement("evaluation:R56c")
@pytest.mark.usefixtures("clean_upload_dir")
@pytest.mark.asyncio
async def test_should_leave_access_roles_unchanged_when_media_is_attached(
    client: AsyncClient, db_session: AsyncSession
):
    """R56c: attaching evidence to an evaluation does not rewrite its privacy."""
    admin_token, coach_token, template_id = await _staff_and_subject(client, db_session)
    [item_id] = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    public = await upload(client, admin_token, access_roles=["public"])
    staff_only = await upload(client, admin_token, access_roles=["admin", "coach"])

    for record in (public, staff_only):
        attached = await client.post(
            f"/v1/evaluations/by_id/{evaluation_id}/media",
            json={"tag": str(item_id), "mediaUuid": record["uuid"]},
            headers=auth(coach_token),
        )
        assert attached.status_code == 201, attached.text

    for record, roles in ((public, ["public"]), (staff_only, ["admin", "coach"])):
        read = await client.get(
            f"/v1/media/by_id/{record['id']}", headers=auth(admin_token)
        )
        assert read.status_code == 200, read.text
        assert sorted(read.json()["accessRoles"]) == roles


@pytest.mark.requirement("evaluation:R58")
@pytest.mark.asyncio
async def test_should_order_duplicate_scope_listing_newest_first_and_stably(
    client: AsyncClient, db_session: AsyncSession
):
    """R58: several evaluations of one scope list in a stable, newest-first order.

    Each covers its own period, so none is another's twin (R7).
    """
    _, coach_token, template_id = await _staff_and_subject(client, db_session)
    ids = [
        await create_general_evaluation(
            client, coach_token, template_id, "alice", **past_period(30 + n, 1)
        )
        for n in range(3)
    ]
    url = "/v1/evaluations?general=true&createdFor=alice"

    first = await client.get(url, headers=auth(coach_token))
    second = await client.get(url, headers=auth(coach_token))

    assert first.status_code == 200, first.text
    listed = [item["id"] for item in first.json()["items"]]
    assert listed == list(reversed(ids))
    assert [item["id"] for item in second.json()["items"]] == listed
