"""Attendance and leave name a real occurrence of a live event (#470).

A time that is not a slot of the event's expanded schedule is refused with
422 ``INVALID_OCCURRENCE_TIME``, so a typo cannot create a record (and a
credit charge) for a session that does not exist. Leave on a soft-deleted
event answers 404, as marking already did.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    mark,
)


async def _register(client: AsyncClient, token: str, event_id: int, slot: int):
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/attendance",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return {row["membername"]: row["status"] for row in response.json()}


async def _member_records(client: AsyncClient, token: str, member: str) -> list:
    response = await client.get(
        f"/v1/myevents/by_id/{member}/attendance",
        params={"fromTimeUtc": at(days=-10), "toTimeUtc": at(days=10)},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _programme_with(
    client: AsyncClient, db_session: AsyncSession, *, start: int
) -> tuple[str, str, int]:
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    await db_session.commit()
    venue = await create_venue(client, admin)
    event = await create_programme(client, admin, venue, start=start)
    assigned = await assign(client, admin, event["id"], "alice")
    assert assigned.status_code in (200, 204), assigned.text
    return admin, alice, event["id"]


async def _request_leave(client: AsyncClient, token: str, event_id: int, slot: int):
    return await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(token),
    )


def _assert_invalid_occurrence_time(response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_OCCURRENCE_TIME"


@pytest.mark.asyncio
async def test_should_return_422_when_marking_a_time_that_is_not_an_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme_with(client, db_session, start=start)
    off_slot = start - 10 * MINUTE_MS

    response = await mark(client, admin, event_id, off_slot, "alice")

    _assert_invalid_occurrence_time(response)
    assert await _register(client, admin, event_id, off_slot) == {}
    assert await _register(client, admin, event_id, start) == {}
    assert await _member_records(client, admin, "alice") == []


@pytest.mark.asyncio
async def test_should_mark_attendance_when_the_time_is_an_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme_with(client, db_session, start=start)

    response = await mark(client, admin, event_id, start, "alice")

    assert response.status_code == 200, response.text
    assert await _register(client, admin, event_id, start) == {"alice": "present"}


@pytest.mark.asyncio
async def test_should_return_422_when_requesting_leave_for_a_time_that_is_not_an_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, alice, event_id = await _programme_with(client, db_session, start=start)
    off_slot = start + DAY_MS // 2

    response = await _request_leave(client, alice, event_id, off_slot)

    _assert_invalid_occurrence_time(response)
    assert await _register(client, admin, event_id, off_slot) == {}
    assert await _member_records(client, alice, "alice") == []


@pytest.mark.asyncio
async def test_should_record_leave_when_the_time_is_an_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, alice, event_id = await _programme_with(client, db_session, start=start)

    response = await _request_leave(client, alice, event_id, start)

    assert response.status_code == 204, response.text
    assert await _register(client, admin, event_id, start) == {
        "alice": "onLeaveRequested"
    }


@pytest.mark.asyncio
async def test_should_return_404_when_requesting_leave_on_a_deleted_event(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, alice, event_id = await _programme_with(client, db_session, start=start)
    deleted = await client.delete(f"/v1/events/by_id/{event_id}", headers=auth(admin))
    assert deleted.status_code == 200, deleted.text

    response = await _request_leave(client, alice, event_id, start)

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"
    assert await _member_records(client, alice, "alice") == []
