"""One-off timetable (#408): ``docs/oneoff_requirements.md`` R22.

The sessions validator has no event-type branch, but every existing
sessions test creates a camp. These exercise the same terms on a
``oneOff``, whose single schedule is edited in place rather than split.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user
from .redesign_helpers import (
    HOUR_MS,
    at,
    auth,
    create_oneoff,
    create_venue,
    reschedule_occurrence,
    version_of,
)

TWO_HALVES = [
    {"name": "First half", "periodMinutes": 30},
    {"name": "Second half", "periodMinutes": 30},
]


async def _reschedule(client: AsyncClient, token: str, event_id: int, **body: object):
    """POST /events/by_id/{id}/reschedule and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={"version": await version_of(client, token, event_id), **body},
        headers=auth(token),
    )


async def _oneoff_with_sessions(
    client: AsyncClient, token: str, venue_id: int
) -> tuple[int, int]:
    """A one-off two days out, one hour long, with two 30-minute sessions."""
    start = at(days=2)
    oneoff = await create_oneoff(
        client, token, venue_id, start=start, sessions=TWO_HALVES
    )
    return oneoff["id"], start


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_round_trip_sessions_when_periods_sum_to_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    oneoff = await create_oneoff(
        client, admin, venue, start=at(days=2), sessions=TWO_HALVES
    )

    assert oneoff["sessions"] == TWO_HALVES


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_reject_creation_when_sessions_do_not_sum_to_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await create_oneoff(
        client,
        admin,
        venue,
        start=at(days=2),
        sessions=[{"name": "Half only", "periodMinutes": 30}],
        expected_status=422,
    )

    assert response["detail"]["code"] == "INVALID_SESSIONS_TOTAL"
    assert "30 min" in response["detail"]["message"]
    assert "60 min" in response["detail"]["message"]


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_reject_creation_when_sessions_are_an_empty_list(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    response = await create_oneoff(
        client, admin, venue, start=at(days=2), sessions=[], expected_status=422
    )

    assert response["detail"]["code"] == "INVALID_SESSIONS_EMPTY"


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_replace_the_timetable_in_place_when_only_sessions_are_sent(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event_id, _ = await _oneoff_with_sessions(client, admin, venue)

    response = await _reschedule(
        client, admin, event_id, sessions=[{"name": "One block", "periodMinutes": 60}]
    )

    assert response.status_code == 200, response.text
    assert response.json()["sessions"] == [{"name": "One block", "periodMinutes": 60}]


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_clear_the_timetable_when_sessions_are_null(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event_id, _ = await _oneoff_with_sessions(client, admin, venue)

    response = await _reschedule(client, admin, event_id, sessions=None)

    assert response.status_code == 200, response.text
    assert response.json()["sessions"] is None


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_reject_reschedule_when_a_longer_window_leaves_sessions_short(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event_id, start = await _oneoff_with_sessions(client, admin, venue)

    # Stretch the occasion to 90 minutes without saying what happens inside it.
    response = await _reschedule(
        client, admin, event_id, endTimeUtc=start + HOUR_MS + 30 * 60 * 1000
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_accept_reschedule_when_window_and_sessions_change_together(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event_id, start = await _oneoff_with_sessions(client, admin, venue)
    stretched = [
        {"name": "First half", "periodMinutes": 45},
        {"name": "Second half", "periodMinutes": 45},
    ]

    response = await _reschedule(
        client,
        admin,
        event_id,
        endTimeUtc=start + 90 * 60 * 1000,
        sessions=stretched,
    )

    assert response.status_code == 200, response.text
    assert response.json()["sessions"] == stretched


@pytest.mark.requirement("oneoff:R22")
@pytest.mark.asyncio
async def test_should_reject_occurrence_reschedule_that_changes_duration_with_a_timetable(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event_id, start = await _oneoff_with_sessions(client, admin, venue)

    # A one-off's occurrence is not rescheduled on its own (#472): the
    # per-occurrence endpoint refuses before any length is considered.
    response = await reschedule_occurrence(
        client, admin, event_id, start, newDurationMinutes=90
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
