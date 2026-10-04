"""A postponed occurrence is live until its new end (#472).

An occurrence moved by an occurrence-level reschedule keeps its original
slot as its key, but the lifecycle predicates — liveness for joins (L12)
and "past" for exits — read the override's new end. A one-off is not moved
this way: its occurrence reschedule answers 422 and the one-off reschedule
moves the schedule itself.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event import Event
from club_server.db.models.occurrence_override import OccurrenceOverride

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    auth,
    create_camp,
    create_oneoff,
    create_venue,
    enrollment_of,
    get_event,
    reschedule_occurrence,
)


async def _occurrence(client: AsyncClient, token: str, event_id: int, slot: int):
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _postponed_camp_whose_slot_has_passed(
    client: AsyncClient, db_session: AsyncSession, *members: str
) -> tuple[str, int, int, int]:
    """A one-day camp postponed to +5 days, its original slot then aged to 3h ago.

    Returns the admin token, event id, the (aged) slot and the new start.
    The slot is moved back in the database, since no endpoint creates a
    past slot; the override moves with it so it still names the slot.
    """
    admin = await create_admin_user(db_session)
    for member in members:
        await create_member_user(db_session, member)
    await db_session.commit()
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, count=1)
    if members:
        assigned = await assign(client, admin, camp["id"], *members)
        assert assigned.status_code in (200, 204), assigned.text
    new_start = at(days=5)
    moved = await reschedule_occurrence(
        client, admin, camp["id"], camp["startTimeUtc"], newStartTimeUtc=new_start
    )
    assert moved.status_code == 204, moved.text

    aged = at(hours=-3)
    row = (
        await db_session.execute(select(Event).where(Event.id == camp["id"]))
    ).scalar_one()
    row.start_time = aged
    row.end_time = aged + HOUR_MS
    override = (
        await db_session.execute(
            select(OccurrenceOverride).where(OccurrenceOverride.event_id == camp["id"])
        )
    ).scalar_one()
    override.occurrence_time = aged
    await db_session.commit()
    db_session.expire_all()
    occurrence = await _occurrence(client, admin, camp["id"], aged)
    assert occurrence["startTimeUtc"] == new_start
    return admin, camp["id"], aged, new_start


@pytest.mark.requirement("lifecycle:L12")
@pytest.mark.asyncio
async def test_should_accept_a_join_when_the_postponed_occurrence_is_still_ahead(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, _, _ = await _postponed_camp_whose_slot_has_passed(
        client, db_session
    )
    regular = await create_regular_admin_user(db_session)
    await create_member_user(db_session, "late_joiner")
    await db_session.commit()

    response = await assign(client, regular, event_id, "late_joiner")

    assert response.status_code in (200, 204), response.text
    assert await enrollment_of(client, admin, event_id, "late_joiner") == "assigned"


@pytest.mark.asyncio
async def test_should_allow_removal_when_the_postponed_occurrence_is_still_ahead(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, _, _ = await _postponed_camp_whose_slot_has_passed(
        client, db_session, "skater"
    )
    regular = await create_regular_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["skater"], "reason": "Moved away"},
        headers=auth(regular),
    )

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, event_id, "skater") == "removed"


@pytest.mark.asyncio
async def test_should_return_422_when_rescheduling_a_oneoff_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)
    slot = oneoff["startTimeUtc"]

    response = await reschedule_occurrence(
        client, admin, oneoff["id"], slot, newStartTimeUtc=slot + HOUR_MS
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    occurrence = await _occurrence(client, admin, oneoff["id"], slot)
    assert occurrence["startTimeUtc"] == slot
    assert occurrence["status"] == "scheduled"
    assert (await get_event(client, admin, oneoff["id"]))["startTimeUtc"] == slot


@pytest.mark.asyncio
async def test_should_reschedule_a_camp_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    camp = await create_camp(client, admin, venue, count=2)
    slot = camp["startTimeUtc"]

    response = await reschedule_occurrence(
        client, admin, camp["id"], slot, newStartTimeUtc=slot + HOUR_MS
    )

    assert response.status_code == 204, response.text
    occurrence = await _occurrence(client, admin, camp["id"], slot)
    assert occurrence["startTimeUtc"] == slot + HOUR_MS
    assert occurrence["status"] == "rescheduled"
