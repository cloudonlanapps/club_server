"""Evaluation scope and eligibility (#302, #535, R1-R6, R28-R33, R58)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_evaluation_response,
    create_event_with_coach,
    create_template,
    days_ago,
    get_evaluation,
    mark_attendance,
    past_period,
    record_past_attendance,
)
from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


async def _event_setup(
    client: AsyncClient, db_session: AsyncSession, *, attend: bool = True
) -> tuple[str, str, int, int, int]:
    """Admin token, coach token, template id, event id, occurrence."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token, name="Event report")
    event_id, occurrence = await create_event_with_coach(
        client, admin_token, coach_names=["coach"]
    )
    if attend:
        await mark_attendance(client, admin_token, event_id, occurrence, "alice")
    return admin_token, coach_token, template_id, event_id, occurrence


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R3")
@pytest.mark.requirement("evaluation:R29")
async def test_should_create_event_scoped_evaluation_when_both_halves_qualify(
    client: AsyncClient, db_session: AsyncSession
):
    """R29: the coach coaches the event and the member attended it."""
    _, coach_token, template_id, event_id, _ = await _event_setup(client, db_session)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert read["eventId"] == event_id


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R29")
@pytest.mark.requirement("evaluation:R32")
async def test_should_reject_event_scope_when_coach_does_not_coach_the_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R32: the coach must be named on the event's coach list."""
    _, _, template_id, event_id, _ = await _event_setup(client, db_session)
    stranger_token = await create_coach_user(db_session, "stranger")
    # Commit the coach: the refused request's rollback would take it along.
    await db_session.commit()

    response = await create_evaluation_response(
        client, stranger_token, template_id, "alice", eventId=event_id
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_ELIGIBLE"
    listed = await client.get("/v1/evaluations", headers=auth(stranger_token))
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R29")
async def test_should_reject_event_scope_when_subject_has_no_attendance_record(
    client: AsyncClient, db_session: AsyncSession
):
    """R29: the member half is required too."""
    _, coach_token, template_id, event_id, _ = await _event_setup(
        client, db_session, attend=False
    )

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_ELIGIBLE"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R31")
async def test_should_accept_absent_attendance_record_as_eligible(
    client: AsyncClient, db_session: AsyncSession
):
    """R31: any attendance record counts, including `absent`."""
    admin_token, coach_token, template_id, event_id, occurrence = await _event_setup(
        client, db_session, attend=False
    )
    await mark_attendance(
        client, admin_token, event_id, occurrence, "alice", status_value="absent"
    )

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R28")
@pytest.mark.requirement("evaluation:R1")
async def test_should_require_no_relationship_for_general_scope(
    client: AsyncClient, db_session: AsyncSession
):
    """R28: any coach may write a general review; R1: naming no event makes it general."""
    admin_token = await create_admin_user(db_session)
    stranger_token = await create_coach_user(db_session, "stranger")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)

    response = await create_evaluation_response(
        client, stranger_token, template_id, "alice"
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, stranger_token, response.json()["id"])
    assert read["eventId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R2")
@pytest.mark.requirement("evaluation:R4")
async def test_should_accept_a_period_on_a_general_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R2, R4 (#535): a general review may cover a period, with no attendance needed."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin_token)

    response = await create_evaluation_response(
        client,
        coach_token,
        template_id,
        "alice",
        periodStartUtc=1_000,
        periodEndUtc=2_000,
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert (read["eventId"], read["periodStartUtc"], read["periodEndUtc"]) == (
        None,
        1_000,
        2_000,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R3")
async def test_should_reject_event_scope_naming_an_unknown_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R3: the event an evaluation is about must exist."""
    _, coach_token, template_id, _, _ = await _event_setup(client, db_session)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=999_999
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
@pytest.mark.parametrize(
    "period",
    [{"periodStartUtc": 1_000}, {"periodStartUtc": 2_000, "periodEndUtc": 1_000}],
    ids=["one_bound", "inverted"],
)
async def test_should_reject_a_malformed_period(
    client: AsyncClient, db_session: AsyncSession, period: dict[str, int]
):
    """R4: a period gives both bounds, and its start is before its end."""
    _, coach_token, template_id, event_id, _ = await _event_setup(client, db_session)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id, **period
    )

    assert response.status_code == 422
    listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R4")
@pytest.mark.requirement("evaluation:R33")
async def test_should_accept_period_covering_the_attended_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """R33: the member half is evaluated within the period.

    The period lies in the past (R4), so the attended session does too.
    """
    _, coach_token, template_id, event_id, _ = await _event_setup(
        client, db_session, attend=False
    )
    occurrence = days_ago(2)
    await record_past_attendance(db_session, event_id, "alice", occurrence)

    response = await create_evaluation_response(
        client,
        coach_token,
        template_id,
        "alice",
        eventId=event_id,
        periodStartUtc=occurrence - 1000,
        periodEndUtc=occurrence + 1000,
    )

    assert response.status_code == 201, response.text
    read = await get_evaluation(client, coach_token, response.json()["id"])
    assert read["periodEndUtc"] == occurrence + 1000


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R6")
@pytest.mark.requirement("evaluation:R33")
async def test_should_reject_period_that_excludes_every_attended_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    """R33: a member who attended outside the period is not eligible within it.

    Also R6: a period covering no occurrences is not rejected on its own
    terms — the emptiness surfaces here, as an eligibility failure. The
    period lies in the past (R4), before the session alice attended.
    """
    _, coach_token, template_id, event_id, _ = await _event_setup(client, db_session)

    response = await create_evaluation_response(
        client,
        coach_token,
        template_id,
        "alice",
        eventId=event_id,
        **past_period(30, 20),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_ELIGIBLE"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R33")
async def test_should_recheck_eligibility_when_a_drafts_period_changes(
    client: AsyncClient, db_session: AsyncSession
):
    """R33: narrowing a draft's period away from every attendance is refused."""
    _, coach_token, template_id, event_id, _ = await _event_setup(client, db_session)
    created = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )
    assert created.status_code == 201, created.text

    response = await client.patch(
        f"/v1/evaluations/by_id/{created.json()['id']}",
        json=past_period(30, 20),
        headers=auth(coach_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_ELIGIBLE"
    read = await get_evaluation(client, coach_token, created.json()["id"])
    assert read["periodStartUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R58")
async def test_should_filter_listing_by_scope_and_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R58: listings can be filtered to general ones, or to one event's.

    The general review covers a period, so it is not the event review's twin (R7).
    """
    _, coach_token, template_id, event_id, _ = await _event_setup(client, db_session)
    event_eval = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )
    general_eval = await create_evaluation_response(
        client, coach_token, template_id, "alice", **past_period(30, 1)
    )
    assert event_eval.status_code == general_eval.status_code == 201

    all_listed = await client.get("/v1/evaluations", headers=auth(coach_token))
    by_event = await client.get(
        f"/v1/evaluations?eventId={event_id}", headers=auth(coach_token)
    )
    general = await client.get(
        "/v1/evaluations?general=true", headers=auth(coach_token)
    )

    assert all_listed.json()["total"] == 2
    assert [e["id"] for e in by_event.json()["items"]] == [event_eval.json()["id"]]
    assert [e["id"] for e in general.json()["items"]] == [general_eval.json()["id"]]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R30")
async def test_should_accept_attendance_at_one_occurrence_of_many(
    client: AsyncClient, db_session: AsyncSession
):
    """R30: one qualifying occurrence is enough; full attendance is not required."""
    _, coach_token, template_id, event_id, _ = await _event_setup(client, db_session)

    response = await create_evaluation_response(
        client, coach_token, template_id, "alice", eventId=event_id
    )

    assert response.status_code == 201, response.text
