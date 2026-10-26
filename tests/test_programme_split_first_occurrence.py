"""A split at the current schedule's first occurrence rewrites that schedule
(#42, programme R26a).

Nothing has run under the current schedule yet, so there is nothing to keep
on the old terms: the schedule takes the new ones in place, and no schedule
is left that ends where it starts. An earlier start moves the schedule's
start back to it, as R24e does for the boundary of a split.
"""

from typing import Any
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    at,
    auth,
    create_programme,
    create_venue,
    list_occurrences,
    split,
)


async def _schedules(
    client: AsyncClient, token: str, event_id: int
) -> list[dict[str, Any]]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/schedules", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _occurrences(
    client: AsyncClient, token: str, event_id: int, from_ms: int, to_ms: int
) -> dict[int, dict[str, Any]]:
    return {
        o["occurrenceTimeUtc"]: o
        for o in await list_occurrences(
            client, token, from_ms, to_ms, event_id=event_id
        )
    }


@pytest.mark.requirement("programme:R26a")
@pytest.mark.asyncio
async def test_should_rewrite_schedule_when_split_at_first_occurrence_of_unstarted_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    done = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=start, venueId=other
    )
    assert done.status_code == 200, done.text

    schedules = await _schedules(client, admin, programme["id"])
    assert len(schedules) == 1
    assert schedules[0]["effectiveFromUtc"] == start
    assert schedules[0]["effectiveUntilUtc"] is None
    assert schedules[0]["venueId"] == other
    listed = await _occurrences(
        client, admin, programme["id"], start - HOUR_MS, start + 2 * DAY_MS
    )
    assert listed[start]["venueId"] == other
    assert listed[start + DAY_MS]["venueId"] == other


@pytest.mark.requirement("programme:R26a")
@pytest.mark.asyncio
async def test_should_keep_first_session_when_unstarted_programme_moves_start_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    earlier = start - HOUR_MS

    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start,
        startTimeUtc=earlier,
        endTimeUtc=earlier + HOUR_MS,
    )
    assert done.status_code == 200, done.text

    schedules = await _schedules(client, admin, programme["id"])
    assert len(schedules) == 1
    assert schedules[0]["effectiveFromUtc"] == earlier
    assert schedules[0]["startTimeUtc"] == earlier
    listed = await _occurrences(
        client, admin, programme["id"], start - DAY_MS, start + 2 * DAY_MS
    )
    assert earlier in listed
    assert start not in listed
    assert earlier + DAY_MS in listed


@pytest.mark.requirement("programme:R26a")
@pytest.mark.asyncio
async def test_should_keep_first_session_when_split_again_at_previous_cutoff_with_earlier_start(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    first_split = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=other
    )
    assert first_split.status_code == 200, first_split.text
    earlier = cutoff - HOUR_MS

    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        startTimeUtc=earlier,
        endTimeUtc=earlier + HOUR_MS,
    )
    assert done.status_code == 200, done.text

    first, second = await _schedules(client, admin, programme["id"])
    assert first["effectiveUntilUtc"] == earlier
    assert second["effectiveFromUtc"] == earlier
    assert (first["venueId"], second["venueId"]) == (venue, other)
    listed = await _occurrences(
        client, admin, programme["id"], cutoff - 2 * DAY_MS, cutoff + 2 * DAY_MS
    )
    assert listed[earlier]["venueId"] == other
    assert cutoff not in listed
    assert listed[cutoff - DAY_MS]["venueId"] == venue


@pytest.mark.requirement("programme:R26a")
@pytest.mark.asyncio
async def test_should_leave_one_schedule_per_period_when_split_again_at_previous_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    third = await create_venue(client, admin, "Rink C")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    first_split = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=other
    )
    assert first_split.status_code == 200, first_split.text

    done = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=third
    )
    assert done.status_code == 200, done.text

    first, second = await _schedules(client, admin, programme["id"])
    assert first["effectiveUntilUtc"] == cutoff
    assert second["effectiveFromUtc"] == cutoff
    assert (first["venueId"], second["venueId"]) == (venue, third)
    assert done.json()["venueId"] == third


@pytest.mark.requirement("programme:R26a")
@pytest.mark.asyncio
async def test_should_reject_split_when_unstarted_programme_moves_start_inside_the_lead_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(minutes=45)
    programme = await create_programme(client, admin, venue, start=start)
    earlier = start - HOUR_MS

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start,
        startTimeUtc=earlier,
        endTimeUtc=earlier + HOUR_MS,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CUTOFF_TOO_SOON"
    schedules = await _schedules(client, admin, programme["id"])
    assert len(schedules) == 1
    assert schedules[0]["startTimeUtc"] == start
