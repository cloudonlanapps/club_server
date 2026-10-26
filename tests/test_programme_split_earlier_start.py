"""A split that moves the start earlier keeps the cutoff day's session (#40,
programme R24e).

The client names the occurrence the new terms begin at as the cutoff, and
sends the new start on that day. When the new start falls before the cutoff,
the new schedule opens at the new start, so its occurrence that day is not
lost between the two schedules. No occurrence of the old schedule moves.
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
    audit_rows,
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


@pytest.mark.requirement("programme:R24e")
@pytest.mark.asyncio
async def test_should_keep_cutoff_day_session_when_split_moves_start_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    earlier = cutoff - HOUR_MS

    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        startTimeUtc=earlier,
        endTimeUtc=earlier + HOUR_MS,
        venueId=other,
    )
    assert done.status_code == 200, done.text

    first, second = await _schedules(client, admin, programme["id"])
    assert first["effectiveUntilUtc"] == earlier
    assert second["effectiveFromUtc"] == earlier
    listed = await _occurrences(
        client, admin, programme["id"], cutoff - 2 * DAY_MS, cutoff + 2 * DAY_MS
    )
    assert listed[earlier]["venueId"] == other
    assert cutoff not in listed
    assert listed[cutoff - DAY_MS]["venueId"] == venue
    assert listed[earlier + DAY_MS]["venueId"] == other


@pytest.mark.requirement("programme:R24e")
@pytest.mark.asyncio
async def test_should_open_new_schedule_at_cutoff_when_split_moves_start_later(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    later = cutoff + HOUR_MS

    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        startTimeUtc=later,
        endTimeUtc=later + HOUR_MS,
    )
    assert done.status_code == 200, done.text

    first, second = await _schedules(client, admin, programme["id"])
    assert first["effectiveUntilUtc"] == cutoff
    assert second["effectiveFromUtc"] == cutoff
    listed = await _occurrences(
        client, admin, programme["id"], cutoff - 2 * DAY_MS, cutoff + 2 * DAY_MS
    )
    assert later in listed
    assert cutoff not in listed
    assert cutoff - DAY_MS in listed


@pytest.mark.requirement("programme:R24e")
@pytest.mark.asyncio
async def test_should_open_new_schedule_at_cutoff_when_earlier_start_reaches_an_old_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    reaching_back = cutoff - DAY_MS - HOUR_MS

    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        startTimeUtc=reaching_back,
        endTimeUtc=reaching_back + HOUR_MS,
        venueId=other,
    )
    assert done.status_code == 200, done.text

    first, second = await _schedules(client, admin, programme["id"])
    assert first["effectiveUntilUtc"] == cutoff
    assert second["effectiveFromUtc"] == cutoff
    listed = await _occurrences(
        client, admin, programme["id"], cutoff - 2 * DAY_MS, cutoff + 2 * DAY_MS
    )
    assert listed[cutoff - DAY_MS]["venueId"] == venue


@pytest.mark.requirement("programme:R24e")
@pytest.mark.asyncio
async def test_should_reject_split_when_earlier_start_is_inside_the_lead_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-3, minutes=45)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    earlier = cutoff - HOUR_MS

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        startTimeUtc=earlier,
        endTimeUtc=earlier + HOUR_MS,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CUTOFF_TOO_SOON"
    schedules = await _schedules(client, admin, programme["id"])
    assert len(schedules) == 1
    assert schedules[0]["effectiveUntilUtc"] is None


@pytest.mark.requirement("programme:R24e")
@pytest.mark.asyncio
async def test_should_audit_applied_boundary_when_split_moves_start_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
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

    splits = await audit_rows(db_session, "split_event")
    assert len(splits) == 1
    assert f'"cutoff": {earlier}' in splits[0].details
