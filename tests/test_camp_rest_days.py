"""A camp's COUNT is the number of days it is held; rest days are not counted (camp R19a, #30)."""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user
from .redesign_helpers import DAY_MS, at, create_event, create_venue, list_occurrences


def _exdate(ms: int) -> str:
    stamp = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return stamp.strftime("%Y%m%dT%H%M%SZ")


@pytest.mark.requirement("camps:R19a")
@pytest.mark.asyncio
async def test_should_hold_count_sessions_across_longer_span_when_camp_has_rest_days(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    rest_days = [start + 2 * DAY_MS, start + 3 * DAY_MS, start + 5 * DAY_MS]
    rule = "FREQ=DAILY;COUNT=7\nEXDATE:" + ",".join(_exdate(d) for d in rest_days)

    camp = await create_event(
        client, admin, event_type="camp", venue_id=venue, start=start, rrule=rule
    )

    occurrences = await list_occurrences(
        client, admin, start - DAY_MS, start + 14 * DAY_MS, event_id=camp["id"]
    )
    assert [o["startTimeUtc"] for o in occurrences] == [
        start + n * DAY_MS for n in (0, 1, 4, 6, 7, 8, 9)
    ]


@pytest.mark.requirement("camps:R19")
@pytest.mark.asyncio
async def test_should_keep_every_slot_when_exdate_is_not_a_session_start(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    rule = f"FREQ=DAILY;COUNT=3\nEXDATE:{_exdate(start + DAY_MS + 60_000)}"

    camp = await create_event(
        client, admin, event_type="camp", venue_id=venue, start=start, rrule=rule
    )

    occurrences = await list_occurrences(
        client, admin, start - DAY_MS, start + 14 * DAY_MS, event_id=camp["id"]
    )
    assert [o["startTimeUtc"] for o in occurrences] == [
        start,
        start + 1 * DAY_MS,
        start + 2 * DAY_MS,
    ]
