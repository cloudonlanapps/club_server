"""A review period lies in the past and may be one day long (#535, R4)."""

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.utils import MS_PER_DAY, now_utc_ms

from .evaluation_helpers import (
    auth,
    create_evaluation_response,
    create_general_evaluation,
    create_template,
    days_ago,
    get_evaluation,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")

HOUR_MS = 3600 * 1000


async def _setup(client: AsyncClient, db_session: AsyncSession) -> tuple[str, int]:
    """A coach token and a template; alice exists."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)
    return coach_token, template_id


def _in_future(response: Response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "PERIOD_IN_FUTURE"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
@pytest.mark.parametrize(
    "start_offset", [-7 * MS_PER_DAY, HOUR_MS], ids=["ends_later", "wholly_ahead"]
)
async def test_should_refuse_creating_a_review_whose_period_ends_in_the_future(
    client: AsyncClient, db_session: AsyncSession, start_offset: int
):
    """R4: a review looks back; its period may not end after now."""
    coach_token, template_id = await _setup(client, db_session)
    now = now_utc_ms()

    response = await create_evaluation_response(
        client,
        coach_token,
        template_id,
        "alice",
        periodStartUtc=now + start_offset,
        periodEndUtc=now + 2 * HOUR_MS,
    )

    _in_future(response)
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
async def test_should_refuse_moving_a_drafts_period_into_the_future(
    client: AsyncClient, db_session: AsyncSession
):
    """R4: the rule holds whenever a draft's period changes."""
    coach_token, template_id = await _setup(client, db_session)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )

    response = await client.patch(
        f"/v1/evaluations/by_id/{evaluation_id}",
        json={"periodStartUtc": days_ago(3), "periodEndUtc": now_utc_ms() + HOUR_MS},
        headers=auth(coach_token),
    )

    _in_future(response)
    read = await get_evaluation(client, coach_token, evaluation_id)
    assert (read["periodStartUtc"], read["periodEndUtc"]) == (None, None)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
@pytest.mark.requirement("evaluation:R5")
async def test_should_accept_a_period_that_ends_where_it_starts(
    client: AsyncClient, db_session: AsyncSession
):
    """R4: a one-day period may end where it starts, on create and update."""
    coach_token, template_id = await _setup(client, db_session)
    day = days_ago(2)
    other_day = days_ago(1)

    created = await create_evaluation_response(
        client, coach_token, template_id, "alice", periodStartUtc=day, periodEndUtc=day
    )
    assert created.status_code == 201, created.text
    updated = await client.patch(
        f"/v1/evaluations/by_id/{created.json()['id']}",
        json={"periodStartUtc": other_day, "periodEndUtc": other_day},
        headers=auth(coach_token),
    )

    assert updated.status_code == 200, updated.text
    read = await get_evaluation(client, coach_token, created.json()["id"])
    assert (read["periodStartUtc"], read["periodEndUtc"]) == (other_day, other_day)


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
async def test_should_accept_a_period_ending_moments_ago(
    client: AsyncClient, db_session: AsyncSession
):
    """R4: a period ending at or before the server's clock is in the past."""
    coach_token, template_id = await _setup(client, db_session)
    end = now_utc_ms() - 1000

    response = await create_evaluation_response(
        client,
        coach_token,
        template_id,
        "alice",
        periodStartUtc=days_ago(90),
        periodEndUtc=end,
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert read["periodEndUtc"] == end
