"""Correcting a timetable in place (#423).

Programme R22c–R22e: ``PATCH .../correction`` accepts ``sessions`` and an
optional ``scheduleId``, replacing one schedule's timetable with no split and
no cutoff. Camp R105 and one-off R22a: the ordinary update accepts
``sessions`` at any time, including after the event has started.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event import Event

from .helpers import create_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    MINUTE_MS,
    at,
    auth,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    get_event,
    split,
    version_of,
)

TWO_HALVES = [
    {"name": "First half", "periodMinutes": 30},
    {"name": "Second half", "periodMinutes": 30},
]
WARM_UP_AND_SKATE = [
    {"name": "Warm-up", "periodMinutes": 20},
    {"name": "Skate", "periodMinutes": 40},
]
NINETY = [
    {"name": "Skate", "periodMinutes": 60},
    {"name": "Game", "periodMinutes": 30},
]


async def _correct(client: AsyncClient, token: str, event_id: int, **body: object):
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}/correction", json=body, headers=auth(token)
    )


async def _update(client: AsyncClient, token: str, event_id: int, **body: object):
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}", json=body, headers=auth(token)
    )


async def _schedules(client: AsyncClient, token: str, event_id: int) -> list[dict]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/schedules", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _split_programme(client: AsyncClient, token: str, venue: int) -> dict:
    """A programme split two days in: 60-minute occurrences, then 90-minute ones."""
    start = at(days=2)
    programme = await create_programme(client, token, venue, start=start)
    cutoff = start + 2 * DAY_MS
    response = await split(
        client,
        token,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        endTimeUtc=start + 90 * MINUTE_MS,
        sessions=NINETY,
    )
    assert response.status_code == 200, response.text
    return programme


async def _drag_into_past(db_session: AsyncSession, event_id: int) -> None:
    """Move an event's window two days back, keeping its length, so it has started.

    No endpoint schedules into the past; this ages the event in place, as the
    reschedule suite does.
    """
    event = await db_session.get(Event, event_id)
    assert event is not None
    length = event.end_time - event.start_time
    event.start_time = at(days=-2)
    event.end_time = event.start_time + length
    await db_session.commit()
    db_session.expire_all()


# ---------------------------------------------------------------------------
# Programme R22c: in place, at any time, latest schedule by default
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R22c")
@pytest.mark.asyncio
async def test_should_correct_sessions_in_place_when_programme_has_started(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-3)
    programme = await create_programme(client, admin, venue, start=start)
    version = programme["version"]

    response = await _correct(
        client, admin, programme["id"], sessions=WARM_UP_AND_SKATE
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["sessions"] == WARM_UP_AND_SKATE
    assert fetched["startTimeUtc"] == start
    assert fetched["endTimeUtc"] == start + HOUR_MS
    assert fetched["version"] == version + 1
    schedules = await _schedules(client, admin, programme["id"])
    assert len(schedules) == 1
    assert schedules[0]["sessions"] == WARM_UP_AND_SKATE


@pytest.mark.requirement("programme:R22c")
@pytest.mark.asyncio
async def test_should_correct_only_the_latest_schedule_when_no_schedule_id_is_given(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await _split_programme(client, admin, venue)
    corrected = [
        {"name": "Warm-up", "periodMinutes": 30},
        {"name": "Game", "periodMinutes": 60},
    ]

    response = await _correct(client, admin, programme["id"], sessions=corrected)

    assert response.status_code == 200, response.text
    first, second = await _schedules(client, admin, programme["id"])
    assert first["sessions"] is None
    assert second["sessions"] == corrected


@pytest.mark.requirement("programme:R22c")
@pytest.mark.asyncio
async def test_should_leave_the_schedule_sequence_untouched_when_sessions_are_corrected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await _split_programme(client, admin, venue)
    before = await _schedules(client, admin, programme["id"])

    response = await _correct(
        client,
        admin,
        programme["id"],
        sessions=[{"name": "One block", "periodMinutes": 90}],
    )

    assert response.status_code == 200, response.text
    after = await _schedules(client, admin, programme["id"])
    assert [s["id"] for s in after] == [s["id"] for s in before]
    for field in (
        "effectiveFromUtc",
        "effectiveUntilUtc",
        "startTimeUtc",
        "endTimeUtc",
    ):
        assert [s[field] for s in after] == [s[field] for s in before]


# ---------------------------------------------------------------------------
# Programme R22d: scheduleId names the schedule
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R22d")
@pytest.mark.asyncio
async def test_should_correct_the_named_schedule_when_schedule_id_is_given(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await _split_programme(client, admin, venue)
    first, _ = await _schedules(client, admin, programme["id"])

    response = await _correct(
        client,
        admin,
        programme["id"],
        scheduleId=first["id"],
        sessions=WARM_UP_AND_SKATE,
    )

    assert response.status_code == 200, response.text
    first, second = await _schedules(client, admin, programme["id"])
    assert first["sessions"] == WARM_UP_AND_SKATE
    assert second["sessions"] == NINETY


@pytest.mark.requirement("programme:R22d")
@pytest.mark.asyncio
async def test_should_reject_correction_when_schedule_id_belongs_to_another_event(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    elsewhere = await create_venue(client, admin, "Second rink")
    other = await create_programme(client, admin, elsewhere, start=at(days=3, hours=3))
    (foreign,) = await _schedules(client, admin, other["id"])
    version = programme["version"]

    response = await _correct(
        client,
        admin,
        programme["id"],
        scheduleId=foreign["id"],
        sessions=WARM_UP_AND_SKATE,
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "SCHEDULE_NOT_FOUND"
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["sessions"] is None
    assert fetched["version"] == version
    (untouched,) = await _schedules(client, admin, other["id"])
    assert untouched["sessions"] is None


@pytest.mark.requirement("programme:R22d")
@pytest.mark.asyncio
async def test_should_reject_correction_when_schedule_id_is_sent_without_sessions(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    (schedule,) = await _schedules(client, admin, programme["id"])

    response = await _correct(
        client, admin, programme["id"], scheduleId=schedule["id"], title="Renamed"
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["title"] == programme["title"]


# ---------------------------------------------------------------------------
# Programme R22e: validated against that schedule's length
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R22e")
@pytest.mark.asyncio
async def test_should_reject_correction_when_sessions_do_not_fit_the_named_schedule(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await _split_programme(client, admin, venue)
    first, _ = await _schedules(client, admin, programme["id"])

    # NINETY fits the latest schedule's 90 minutes, not the first's 60.
    response = await _correct(
        client, admin, programme["id"], scheduleId=first["id"], sessions=NINETY
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"
    first, second = await _schedules(client, admin, programme["id"])
    assert first["sessions"] is None
    assert second["sessions"] == NINETY


@pytest.mark.requirement("programme:R22e")
@pytest.mark.asyncio
async def test_should_reject_correction_when_sessions_do_not_fit_the_latest_schedule(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await _split_programme(client, admin, venue)

    # WARM_UP_AND_SKATE totals 60 minutes; the latest schedule runs 90.
    response = await _correct(
        client, admin, programme["id"], sessions=WARM_UP_AND_SKATE
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"
    _, second = await _schedules(client, admin, programme["id"])
    assert second["sessions"] == NINETY


@pytest.mark.requirement("programme:R22e")
@pytest.mark.asyncio
async def test_should_clear_the_timetable_when_corrected_sessions_are_null(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await _split_programme(client, admin, venue)

    response = await _correct(client, admin, programme["id"], sessions=None)

    assert response.status_code == 200, response.text
    _, second = await _schedules(client, admin, programme["id"])
    assert second["sessions"] is None
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["sessions"] is None


# ---------------------------------------------------------------------------
# Camp R105 and one-off R22a: the ordinary update, at any time
# ---------------------------------------------------------------------------


@pytest.mark.requirement("camps:R105")
@pytest.mark.asyncio
async def test_should_correct_camp_sessions_through_update_when_camp_has_started(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, sessions=TWO_HALVES)
    await _drag_into_past(db_session, camp["id"])
    before = await get_event(client, admin, camp["id"])

    response = await _update(client, admin, camp["id"], sessions=WARM_UP_AND_SKATE)

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["sessions"] == WARM_UP_AND_SKATE
    assert fetched["startTimeUtc"] == before["startTimeUtc"]
    assert fetched["endTimeUtc"] == before["endTimeUtc"]
    assert fetched["version"] == before["version"] + 1


@pytest.mark.requirement("camps:R105")
@pytest.mark.asyncio
async def test_should_reject_camp_sessions_update_when_they_do_not_fit_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, sessions=TWO_HALVES)

    response = await _update(client, admin, camp["id"], sessions=NINETY)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"
    fetched = await get_event(client, admin, camp["id"])
    assert fetched["sessions"] == TWO_HALVES


@pytest.mark.requirement("oneoff:R22a")
@pytest.mark.asyncio
async def test_should_correct_oneoff_sessions_through_update_when_it_has_started(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue, sessions=TWO_HALVES)
    await _drag_into_past(db_session, oneoff["id"])
    before = await get_event(client, admin, oneoff["id"])

    response = await _update(client, admin, oneoff["id"], sessions=WARM_UP_AND_SKATE)

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, oneoff["id"])
    assert fetched["sessions"] == WARM_UP_AND_SKATE
    assert fetched["startTimeUtc"] == before["startTimeUtc"]
    assert fetched["endTimeUtc"] == before["endTimeUtc"]
    assert fetched["version"] == before["version"] + 1


@pytest.mark.requirement("oneoff:R22a")
@pytest.mark.asyncio
async def test_should_reject_oneoff_sessions_update_when_they_do_not_fit_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue, sessions=TWO_HALVES)

    response = await _update(client, admin, oneoff["id"], sessions=NINETY)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"
    fetched = await get_event(client, admin, oneoff["id"])
    assert fetched["sessions"] == TWO_HALVES
