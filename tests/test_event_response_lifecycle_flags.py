"""Every event response carries ``isPast`` and ``hasEnded`` (#53).

``docs/event_lifecycle_requirements.md`` L24. ``hasEnded`` is L7 (no live
occurrence at or after now, the join side, L12); ``isPast`` is every
occurrence finished, cancelled or not (the exit side). The server answers
both so a client does not expand the rule itself.

No endpoint creates an event in the past, so a test that needs one moves
its schedule in the database, as the attendance suite does.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event import Event

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    assign,
    at,
    auth,
    cancel_occurrence,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    floor_s,
    get_event,
    set_cutoff,
)


async def _move_to(db_session: AsyncSession, event_id: int, start: int) -> None:
    """Move an event's schedule so its first occurrence starts at ``start``."""
    event = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event.start_time = start
    event.end_time = start + HOUR_MS
    await db_session.commit()
    db_session.expire_all()


async def _flags(client: AsyncClient, token: str, event_id: int) -> tuple[bool, bool]:
    body = await get_event(client, token, event_id)
    return body["isPast"], body["hasEnded"]


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_not_past_and_not_ended_when_oneoff_is_ahead(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    oneoff = await create_oneoff(client, super_admin, venue, start=at(days=2))

    assert await _flags(client, admin, oneoff["id"]) == (False, False)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_past_and_ended_when_oneoff_has_finished(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    oneoff = await create_oneoff(client, super_admin, venue, start=at(days=2))
    await _move_to(db_session, oneoff["id"], at(hours=-3))

    assert await _flags(client, admin, oneoff["id"]) == (True, True)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_not_past_and_not_ended_when_camp_is_midway(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    camp = await create_camp(client, super_admin, venue, start=floor_s(at(days=2)))
    await _move_to(db_session, camp["id"], floor_s(at(days=-2)))

    assert await _flags(client, admin, camp["id"]) == (False, False)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_past_and_ended_when_camp_last_day_has_run(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    camp = await create_camp(client, super_admin, venue, start=floor_s(at(days=2)))
    await _move_to(db_session, camp["id"], floor_s(at(days=-10)))

    assert await _flags(client, admin, camp["id"]) == (True, True)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_not_past_and_not_ended_when_open_programme_runs_for_years(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    programme = await create_programme(client, super_admin, venue)
    await _move_to(db_session, programme["id"], at(days=-800))

    assert await _flags(client, admin, programme["id"]) == (False, False)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_ended_but_not_past_when_programme_cutoff_has_passed(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    programme = await create_programme(client, super_admin, venue)
    start = at(days=-30)
    await _move_to(db_session, programme["id"], start)
    await set_cutoff(db_session, programme["id"], start + 10 * DAY_MS)

    assert await _flags(client, admin, programme["id"]) == (False, True)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_report_ended_but_not_past_when_every_remaining_camp_day_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = floor_s(at(days=2))
    camp = await create_camp(client, super_admin, venue, start=start, count=2)
    for slot in (start, start + DAY_MS):
        response = await cancel_occurrence(client, admin, camp["id"], slot)
        assert response.status_code == 204, response.text

    assert await _flags(client, admin, camp["id"]) == (False, True)


@pytest.mark.requirement("lifecycle:L24")
@pytest.mark.asyncio
async def test_should_carry_flags_on_event_listings(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    ahead = await create_oneoff(client, super_admin, venue, start=at(days=2))
    done = await create_oneoff(
        client, super_admin, venue, start=at(days=3), title="Done"
    )
    for event_id in (ahead["id"], done["id"]):
        assert (await assign(client, admin, event_id, "skater")).status_code == 204
    await _move_to(db_session, done["id"], at(hours=-3))
    expected = {ahead["id"]: (False, False), done["id"]: (True, True)}

    staff = await client.get("/v1/events", headers=auth(admin))
    mine = await client.get("/v1/myevents/by_id/skater", headers=auth(member))

    for response in (staff, mine):
        assert response.status_code == 200, response.text
        got = {
            e["id"]: (e["isPast"], e["hasEnded"])
            for e in response.json()["items"]
            if e["id"] in expected
        }
        assert got == expected
