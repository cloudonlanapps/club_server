"""A cancelled camp can be cancelled again from an earlier day (#51).

``docs/camps_requirements.md`` R5e. Cancelling from an earlier occurrence
start moves the cutoff earlier under R5c's rules; a cutoff at or after the
current one is still refused, since moving the end later is undo (R5d).
"""

from typing import Any
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    cancel_series,
    create_camp,
    create_venue,
    get_event,
    list_occurrences,
    list_user_occurrences,
    notifications_for,
)


async def _cancelled_camp(
    client: AsyncClient, super_admin: str, admin: str, *, start: int
) -> dict[str, Any]:
    """A 7-day camp with ``skater`` assigned, cancelled from day 5."""
    venue = await create_venue(client, super_admin)
    camp = await create_camp(client, super_admin, venue, start=start, count=7)
    assert (await assign(client, admin, camp["id"], "skater")).status_code == 204
    first = await cancel_series(client, admin, camp["id"], start + 4 * DAY_MS)
    assert first.status_code == 200, first.text
    return camp


@pytest.mark.requirement("camps:R5e")
@pytest.mark.asyncio
async def test_should_move_cutoff_earlier_when_cancelled_camp_cancelled_from_earlier_day(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    start = at(days=2)
    camp = await _cancelled_camp(client, super_admin, admin, start=start)

    response = await cancel_series(client, admin, camp["id"], start + DAY_MS)

    assert response.status_code == 200, response.text
    assert (await get_event(client, admin, camp["id"]))["untilTimeUtc"] == (
        start + DAY_MS
    )
    window = (start - HOUR_MS, start + 8 * DAY_MS)
    admin_view = await list_occurrences(client, admin, *window, event_id=camp["id"])
    assert [o["status"] for o in admin_view] == ["scheduled"] + ["cancelled"] * 6
    member_view = await list_user_occurrences(
        client, member, "skater", *window, event_id=camp["id"]
    )
    assert [o["status"] for o in member_view] == ["scheduled"] + ["cancelled"] * 6


@pytest.mark.requirement("camps:R5e")
@pytest.mark.asyncio
async def test_should_notify_members_when_cancelled_camp_cutoff_moved_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    start = at(days=2)
    camp = await _cancelled_camp(client, super_admin, admin, start=start)

    response = await cancel_series(client, admin, camp["id"], start + DAY_MS)

    assert response.status_code == 200, response.text
    cancelled = await notifications_for(db_session, "skater", "event.cancelled")
    assert sorted(n.payload["data"]["effectiveTimeUtc"] for n in cancelled) == [
        start + DAY_MS,
        start + 4 * DAY_MS,
    ]


@pytest.mark.requirement("camps:R5e")
@pytest.mark.asyncio
async def test_should_refuse_cancel_when_cancelled_camp_cutoff_would_move_later(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    start = at(days=2)
    camp = await _cancelled_camp(client, super_admin, admin, start=start)

    response = await cancel_series(client, admin, camp["id"], start + 5 * DAY_MS)

    assert response.status_code == 400, response.text
    assert "EVENT_ALREADY_CANCELLED" in str(response.json()["detail"])
    assert (await get_event(client, admin, camp["id"]))["untilTimeUtc"] == (
        start + 4 * DAY_MS
    )


@pytest.mark.requirement("camps:R5e")
@pytest.mark.asyncio
async def test_should_refuse_earlier_cancel_when_effective_time_is_not_an_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    start = at(days=2)
    camp = await _cancelled_camp(client, super_admin, admin, start=start)

    response = await cancel_series(
        client, admin, camp["id"], start + DAY_MS + 30 * MINUTE_MS
    )

    assert response.status_code == 400, response.text
    assert "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY" in str(response.json()["detail"])
    assert (await get_event(client, admin, camp["id"]))["untilTimeUtc"] == (
        start + 4 * DAY_MS
    )
