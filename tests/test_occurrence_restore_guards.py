"""Undoing an occurrence cancellation has cancel's guards (#474).

A cancelled occurrence can be restored only while it could still be
cancelled: not once it has passed (422 ``PAST_OCCURRENCE``) and not inside
the lead time (400 ``CANCELLATION_LEAD_TIME_VIOLATED``). A super-admin may
override both, and — as with an override cancel — that restore sends no
notification.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event import Event

from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    auth,
    cancel_occurrence,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    notifications_for,
    undo_cancel_occurrence,
)


async def _status_of(client: AsyncClient, token: str, event_id: int, slot: int) -> str:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["status"]


async def _move_start(db_session: AsyncSession, event_id: int, start: int) -> None:
    """Move an event's first slot, since no endpoint puts one in the past."""
    row = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    row.start_time = start
    row.end_time = start + HOUR_MS
    await db_session.commit()
    db_session.expire_all()


async def _move_cancelled_override(
    db_session: AsyncSession, event_id: int, slot: int
) -> None:
    from club_server.db.models.occurrence_override import OccurrenceOverride

    override = (
        await db_session.execute(
            select(OccurrenceOverride).where(OccurrenceOverride.event_id == event_id)
        )
    ).scalar_one()
    override.occurrence_time = slot
    await db_session.commit()
    db_session.expire_all()


async def _imminent_cancelled_programme(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int]:
    """A programme starting in 20 minutes whose first slot a super-admin cancelled."""
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    await create_member_user(db_session, "skater")
    await db_session.commit()
    venue = await create_venue(client, admin)
    start = at(minutes=20)
    event = await create_programme(client, admin, venue, start=start)
    assigned = await assign(client, admin, event["id"], "skater")
    assert assigned.status_code in (200, 204), assigned.text
    cancelled = await cancel_occurrence(client, admin, event["id"], start)
    assert cancelled.status_code == 204, cancelled.text
    return admin, regular, event["id"], start


@pytest.mark.asyncio
async def test_should_return_400_when_restoring_inside_the_lead_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin, regular, event_id, slot = await _imminent_cancelled_programme(
        client, db_session
    )

    response = await undo_cancel_occurrence(client, regular, event_id, slot)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "CANCELLATION_LEAD_TIME_VIOLATED"
    assert await _status_of(client, admin, event_id, slot) == "cancelled"


@pytest.mark.requirement("notifications:R84")
@pytest.mark.asyncio
async def test_should_restore_without_notifying_when_super_admin_overrides_the_lead_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, event_id, slot = await _imminent_cancelled_programme(client, db_session)

    response = await undo_cancel_occurrence(client, admin, event_id, slot)

    assert response.status_code == 204, response.text
    assert await _status_of(client, admin, event_id, slot) == "scheduled"
    assert await notifications_for(db_session, "skater", "occurrence.restored") == []
    assert await notifications_for(db_session, "skater", "occurrence.cancelled") == []


@pytest.mark.asyncio
async def test_should_return_422_when_restoring_a_past_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    await db_session.commit()
    venue = await create_venue(client, admin)
    event = await create_programme(client, admin, venue)
    first = event["startTimeUtc"]
    cancelled = await cancel_occurrence(client, admin, event["id"], first)
    assert cancelled.status_code == 204, cancelled.text
    past = at(days=-1)
    await _move_start(db_session, event["id"], past)
    await _move_cancelled_override(db_session, event["id"], past)

    response = await undo_cancel_occurrence(client, regular, event["id"], past)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "PAST_OCCURRENCE"
    assert await _status_of(client, admin, event["id"], past) == "cancelled"


@pytest.mark.asyncio
async def test_should_return_422_when_restoring_a_dropped_oneoff_that_has_started(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    await db_session.commit()
    venue = await create_venue(client, admin)
    oneoff = await create_oneoff(client, admin, venue)
    dropped = await drop(client, admin, oneoff["id"])
    assert dropped.status_code == 200, dropped.text
    started = at(minutes=-10)
    await _move_start(db_session, oneoff["id"], started)
    await _move_cancelled_override(db_session, oneoff["id"], started)

    response = await undo_cancel_occurrence(client, regular, oneoff["id"], started)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "PAST_OCCURRENCE"
    assert await _status_of(client, admin, oneoff["id"], started) == "cancelled"


@pytest.mark.requirement("notifications:R83")
@pytest.mark.asyncio
async def test_should_restore_and_notify_when_outside_the_lead_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    await create_member_user(db_session, "skater")
    await db_session.commit()
    venue = await create_venue(client, admin)
    event = await create_programme(client, admin, venue)
    slot = event["startTimeUtc"]
    assigned = await assign(client, admin, event["id"], "skater")
    assert assigned.status_code in (200, 204), assigned.text
    cancelled = await cancel_occurrence(client, regular, event["id"], slot)
    assert cancelled.status_code == 204, cancelled.text
    assert (
        len(await notifications_for(db_session, "skater", "occurrence.cancelled")) == 1
    )

    response = await undo_cancel_occurrence(client, regular, event["id"], slot)

    assert response.status_code == 204, response.text
    assert await _status_of(client, admin, event["id"], slot) == "scheduled"
