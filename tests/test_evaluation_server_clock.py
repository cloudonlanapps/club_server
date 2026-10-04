"""Evaluations and templates take their timestamps from the server clock (#435).

`referenceDateTimeUtc` was removed from every request body in May; the
evaluations module brought it back on six requests, letting a client backdate
or future-date what it writes. Each write request refuses the field with 422,
as every other route does (``extra="forbid"``), and nothing is written. #535
reshaped the writes; the rule carries over to every one of them.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.utils import now_utc_ms

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    get_evaluation,
    get_template,
    item_ids,
    qa_item,
    rating_item,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")

BACKDATED = 1_600_000_000_000  # Sep 2020


def _refused_for_reference_time(response: Response) -> None:
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any(
        e["loc"][-1] == "referenceDateTimeUtc" and e["type"] == "extra_forbidden"
        for e in errors
    ), errors


async def _setup(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int, int]:
    """Admin and coach tokens, the default template, its item, a draft about alice."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_coach_user(db_session, "coach_b")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    [item_id] = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    return admin_token, coach_token, template_id, item_id, evaluation_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_name",
    ["create", "update", "answer", "transfer"],
)
async def test_should_reject_evaluation_writes_when_reference_time_is_sent(
    client: AsyncClient, db_session: AsyncSession, request_name: str
):
    _, coach_token, template_id, item_id, evaluation_id = await _setup(
        client, db_session
    )
    base = f"/v1/evaluations/by_id/{evaluation_id}"
    requests: dict[str, tuple[str, str, dict[str, Any]]] = {
        "create": (
            "POST",
            "/v1/evaluations",
            {"templateId": template_id, "createdFor": "alice"},
        ),
        "update": ("PATCH", base, {"periodStartUtc": 1, "periodEndUtc": 2}),
        "answer": ("PUT", f"{base}/answers/{item_id}", {"valueNum": 3}),
        "transfer": ("POST", f"{base}/transfer", {"owner": "coach_b"}),
    }
    method, url, body = requests[request_name]

    response = await client.request(
        method,
        url,
        json={**body, "referenceDateTimeUtc": BACKDATED},
        headers=auth(coach_token),
    )

    _refused_for_reference_time(response)
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert (read["periodStartUtc"], read["answers"], read["owner"]) == (None, [], None)
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("request_name", ["create", "update", "add_item", "put_item"])
async def test_should_reject_template_writes_when_reference_time_is_sent(
    client: AsyncClient, db_session: AsyncSession, request_name: str
):
    admin_token = await create_admin_user(db_session)
    # A successful request first: it commits the admin, which a refused
    # request's rollback would otherwise take with it.
    template_id = await create_template(client, admin_token, name="Existing")
    [item_id] = await item_ids(client, admin_token, template_id)
    base = f"/v1/evaluations/templates/by_id/{template_id}"
    requests: dict[str, tuple[str, str, dict[str, Any]]] = {
        "create": (
            "POST",
            "/v1/evaluations/templates",
            {"name": "Backdated", "layout": [rating_item()]},
        ),
        "update": ("PATCH", base, {"name": "After"}),
        "add_item": ("POST", f"{base}/items", {"item": qa_item()}),
        "put_item": ("PUT", f"{base}/items/{item_id}", rating_item("Edges")),
    }
    method, url, body = requests[request_name]

    response = await client.request(
        method,
        url,
        json={**body, "referenceDateTimeUtc": BACKDATED},
        headers=auth(admin_token),
    )

    _refused_for_reference_time(response)
    listed = await client.get("/v1/evaluations/templates", headers=auth(admin_token))
    assert [t["name"] for t in listed.json()["items"]] == ["Existing"]
    read = await get_template(client, admin_token, template_id)
    assert [i["question"] for i in read["items"]] == ["Skating"]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R13")
async def test_should_stamp_evaluation_with_server_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    before = now_utc_ms()

    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    after = now_utc_ms()
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert before <= read["createdAtUtc"] <= after
    assert before <= read["updatedAtUtc"] <= after


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R13")
async def test_should_stamp_template_with_server_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    before = now_utc_ms()

    template_id = await create_template(client, admin_token)

    after = now_utc_ms()
    read = await get_template(client, admin_token, template_id)
    assert before <= read["createdAtUtc"] <= after
    assert before <= read["updatedAtUtc"] <= after
