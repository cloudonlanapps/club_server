"""A schedule clash is reported, and the organizer decides (#72, programme R31d).

Creation and split: any finding on the venue, organizer or coach gate
answers 409 ``SCHEDULE_CONFLICT`` with the full report and writes nothing;
the same request with ``acceptConflicts: true`` is written and the admins
are told, as an ``event.conflict_detected`` notice.
"""

from typing import Any
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user
from .redesign_helpers import (
    HOUR_MS,
    WEEK_MS,
    at,
    auth,
    create_programme,
    create_venue,
    get_event,
    notifications_for,
    split,
    weekday_code,
)

SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"


async def _conflict_notices(db_session: AsyncSession) -> list[Any]:
    return await notifications_for(db_session, "admin", "event.conflict_detected")


def _body(event_type: str, venue: int, start: int, **extra: object) -> dict[str, Any]:
    body: dict[str, object] = {
        "title": f"{event_type} clashing",
        "type": event_type,
        "venueId": venue,
        "startTimeUtc": start,
        "endTimeUtc": start + HOUR_MS,
    }
    if event_type == "programme":
        body["rrule"] = f"FREQ=WEEKLY;BYDAY={weekday_code(start)}"
    elif event_type == "camp":
        body["rrule"] = "FREQ=DAILY;COUNT=3"
    body.update(extra)
    return body


async def _listed_ids(client: AsyncClient, token: str) -> list[int]:
    listed = await client.get("/v1/events", headers=auth(token))
    assert listed.status_code == 200, listed.text
    return sorted(e["id"] for e in listed.json()["items"])


# ---------------------------------------------------------------------------
# Creation, every type
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R31d")
@pytest.mark.parametrize("event_type", ["programme", "camp", "oneOff"])
@pytest.mark.asyncio
async def test_should_answer_409_and_write_nothing_when_created_event_clashes(
    client: AsyncClient, db_session: AsyncSession, event_type: str
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=3)
    held = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        rrule=f"FREQ=WEEKLY;BYDAY={weekday_code(start)}",
    )

    response = await client.post(
        "/v1/events", json=_body(event_type, venue, start), headers=auth(admin)
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert [c["eventId"] for c in detail["venueConflicts"]] == [held["id"]]
    assert detail["venueConflicts"][0]["occurrences"][0]["otherStartUtc"] == start
    assert await _listed_ids(client, admin) == [held["id"]]
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("programme:R31d")
@pytest.mark.parametrize("event_type", ["programme", "camp", "oneOff"])
@pytest.mark.asyncio
async def test_should_create_and_notify_admins_when_clash_is_accepted(
    client: AsyncClient, db_session: AsyncSession, event_type: str
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=3)
    held = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        rrule=f"FREQ=WEEKLY;BYDAY={weekday_code(start)}",
    )

    response = await client.post(
        "/v1/events",
        json=_body(event_type, venue, start, acceptConflicts=True),
        headers=auth(admin),
    )

    assert response.status_code == 201, response.text
    created = response.json()["id"]
    assert await _listed_ids(client, admin) == sorted([held["id"], created])
    notices = await _conflict_notices(db_session)
    assert [n.payload["data"]["eventId"] for n in notices] == [created]
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        held["id"]
    ]


@pytest.mark.requirement("programme:R31d")
@pytest.mark.asyncio
async def test_should_report_venue_organizer_and_coach_when_all_three_gates_find(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    start = at(days=3)
    held = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        rrule=f"FREQ=WEEKLY;BYDAY={weekday_code(start)}",
        coachNames=["bob"],
    )

    response = await client.post(
        "/v1/events",
        json=_body("camp", venue, start, coachNames=["bob"]),
        headers=auth(admin),
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    for gate in ("venueConflicts", "organizerConflicts", "coachConflicts"):
        assert [c["eventId"] for c in detail[gate]] == [held["id"]], gate
        assert detail[gate][0]["eventType"] == "programme"
        assert detail[gate][0]["occurrences"][0]["targetStartUtc"] == start
    assert await _listed_ids(client, admin) == [held["id"]]


@pytest.mark.requirement("programme:R31d")
@pytest.mark.asyncio
async def test_should_answer_409_with_coach_conflicts_when_only_the_coach_clashes(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "bob")
    _ = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=3)
    held = await create_programme(
        client,
        admin,
        venue,
        start=start,
        end=start + HOUR_MS,
        rrule=f"FREQ=WEEKLY;BYDAY={weekday_code(start)}",
        coachNames=["bob"],
    )

    response = await client.post(
        "/v1/events",
        json=_body(
            "programme",
            other,
            start,
            organizerName="other_organizer",
            coachNames=["bob"],
        ),
        headers=auth(admin),
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert detail["venueConflicts"] == []
    assert detail["organizerConflicts"] == []
    assert [c["eventId"] for c in detail["coachConflicts"]] == [held["id"]]
    assert await _listed_ids(client, admin) == [held["id"]]


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


async def _two_programmes(client: AsyncClient, db_session: AsyncSession):
    """A programme at Rink A and another, run by someone else, at Rink B."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=3)
    rule = f"FREQ=WEEKLY;BYDAY={weekday_code(start)}"
    programme = await create_programme(
        client, admin, venue, start=start, end=start + HOUR_MS, rrule=rule
    )
    occupied = await create_programme(
        client,
        admin,
        other,
        start=start,
        end=start + HOUR_MS,
        rrule=rule,
        organizerName="other_organizer",
    )
    return admin, venue, other, start, programme, occupied


async def _schedule_count(client: AsyncClient, token: str, event_id: int) -> int:
    listed = await client.get(
        f"/v1/events/by_id/{event_id}/schedules", headers=auth(token)
    )
    assert listed.status_code == 200, listed.text
    return len(listed.json())


@pytest.mark.requirement("programme:R31d")
@pytest.mark.asyncio
async def test_should_answer_409_and_keep_schedule_when_split_clashes(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue, other, start, programme, occupied = await _two_programmes(
        client, db_session
    )

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 2 * WEEK_MS,
        venueId=other,
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == SCHEDULE_CONFLICT
    assert [c["eventId"] for c in detail["venueConflicts"]] == [occupied["id"]]
    assert (await get_event(client, admin, programme["id"]))["venueId"] == venue
    assert await _schedule_count(client, admin, programme["id"]) == 1
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("programme:R31d")
@pytest.mark.asyncio
async def test_should_split_and_notify_admins_when_split_clash_is_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, other, start, programme, occupied = await _two_programmes(
        client, db_session
    )

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 2 * WEEK_MS,
        venueId=other,
        acceptConflicts=True,
    )

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, programme["id"]))["venueId"] == other
    assert await _schedule_count(client, admin, programme["id"]) == 2
    notices = await _conflict_notices(db_session)
    assert [n.payload["data"]["eventId"] for n in notices] == [programme["id"]]
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        occupied["id"]
    ]


@pytest.mark.requirement("programme:R31d")
@pytest.mark.asyncio
async def test_should_answer_409_when_split_adds_a_coach_who_is_busy(
    client: AsyncClient, db_session: AsyncSession
):
    """The coach gate's finding on a split was computed and discarded before #72."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "bob")
    _ = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=3)
    rule = f"FREQ=WEEKLY;BYDAY={weekday_code(start)}"
    programme = await create_programme(
        client, admin, venue, start=start, end=start + HOUR_MS, rrule=rule
    )
    coached = await create_programme(
        client,
        admin,
        other,
        start=start,
        end=start + HOUR_MS,
        rrule=rule,
        organizerName="other_organizer",
        coachNames=["bob"],
    )

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 2 * WEEK_MS,
        coachNames=["bob"],
    )

    assert response.status_code == 409, response.text
    assert [c["eventId"] for c in response.json()["detail"]["coachConflicts"]] == [
        coached["id"]
    ]
    assert (await get_event(client, admin, programme["id"]))["coachNames"] in (
        None,
        [],
    )
    assert await _schedule_count(client, admin, programme["id"]) == 1
