"""The staff occurrence list counts each session's open requests (#153).

``GET /events/occurrences`` gives every occurrence ``leaveRequestsPending``
(this session's leave requests awaiting a decision),
``enrollmentRequestsPending`` and ``withdrawalRequestsPending`` (the event's
enrollments in ``requested`` and ``withdrawRequested``). They come from the
same queries as the attendance counts. Other occurrence responses leave them
``null``.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    list_occurrences,
)

pytestmark = pytest.mark.requirement("attendance:R38b")

MEMBERS = ("alice", "bob", "carol")


async def _programme(
    client: AsyncClient, db_session: AsyncSession, *, start: int
) -> tuple[str, dict[str, str], int]:
    """A public daily programme starting at ``start``; alice and bob are
    assigned, carol is not enrolled."""
    admin = await create_admin_user(db_session)
    tokens = {m: await create_member_user(db_session, m) for m in MEMBERS}
    await db_session.commit()
    venue = await create_venue(client, admin)
    programme = await create_programme(
        client, admin, venue, start=start, visibility="public"
    )
    assigned = await assign(client, admin, programme["id"], "alice", "bob")
    assert assigned.status_code in (200, 204), assigned.text
    return admin, tokens, programme["id"]


async def _occurrence(
    client: AsyncClient, token: str, event_id: int, slot: int
) -> dict[str, Any]:
    """The listed occurrence of ``event_id`` at ``slot``."""
    items = await list_occurrences(
        client, token, slot - MINUTE_MS, slot + MINUTE_MS, event_id=event_id
    )
    matching = [o for o in items if o["occurrenceTimeUtc"] == slot]
    assert len(matching) == 1, items
    return matching[0]


def _pending(occurrence: dict[str, Any]) -> tuple[int, int, int]:
    return (
        occurrence["leaveRequestsPending"],
        occurrence["enrollmentRequestsPending"],
        occurrence["withdrawalRequestsPending"],
    )


async def _request_leave(
    client: AsyncClient, token: str, member: str, event_id: int, slot: int
) -> None:
    response = await client.post(
        f"/v1/myevents/by_id/{member}/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(token),
    )
    assert response.status_code == 204, response.text


async def _decide_leave(
    client: AsyncClient, admin: str, event_id: int, slot: int, verb: str
) -> None:
    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/{verb}",
        json={"membernames": ["alice"]},
        headers=auth(admin),
    )
    assert response.status_code in (200, 204), response.text


async def _member_post(
    client: AsyncClient, token: str, member: str, event_id: int, verb: str
) -> None:
    response = await client.post(
        f"/v1/myevents/by_id/{member}/{event_id}/enrollments/{verb}",
        json={},
        headers=auth(token),
    )
    assert response.status_code in (200, 201, 204), response.text


@pytest.mark.asyncio
async def test_should_count_no_pending_requests_when_none_are_open(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, _, event_id = await _programme(client, db_session, start=start)

    assert _pending(await _occurrence(client, admin, event_id, start)) == (0, 0, 0)


@pytest.mark.asyncio
async def test_should_count_a_pending_leave_request_on_its_session_only(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)

    await _request_leave(client, tokens["alice"], "alice", event_id, start)

    assert _pending(await _occurrence(client, admin, event_id, start)) == (1, 0, 0)
    next_day = await _occurrence(client, admin, event_id, start + DAY_MS)
    assert _pending(next_day) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_should_not_count_leave_as_pending_when_it_is_decided(
    client: AsyncClient, db_session: AsyncSession, verb: str
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)
    await _request_leave(client, tokens["alice"], "alice", event_id, start)
    assert _pending(await _occurrence(client, admin, event_id, start)) == (1, 0, 0)

    await _decide_leave(client, admin, event_id, start, verb)

    assert _pending(await _occurrence(client, admin, event_id, start)) == (0, 0, 0)


@pytest.mark.asyncio
async def test_should_count_an_enrollment_request_on_every_session_of_the_event(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)

    await _member_post(client, tokens["carol"], "carol", event_id, "request")

    for slot in (start, start + DAY_MS):
        occurrence = await _occurrence(client, admin, event_id, slot)
        assert _pending(occurrence) == (0, 1, 0)


@pytest.mark.asyncio
async def test_should_count_a_withdrawal_request_on_every_session_of_the_event(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)

    await _member_post(client, tokens["bob"], "bob", event_id, "withdraw")

    for slot in (start, start + DAY_MS):
        occurrence = await _occurrence(client, admin, event_id, slot)
        assert _pending(occurrence) == (0, 0, 1)


@pytest.mark.asyncio
async def test_should_give_a_coach_the_same_pending_counts_when_listing_occurrences(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)
    coach = await create_coach_user(db_session)
    await db_session.commit()
    await _request_leave(client, tokens["alice"], "alice", event_id, start)
    await _member_post(client, tokens["carol"], "carol", event_id, "request")
    await _member_post(client, tokens["bob"], "bob", event_id, "withdraw")

    as_admin = await _occurrence(client, admin, event_id, start)
    as_coach = await _occurrence(client, coach, event_id, start)

    assert _pending(as_admin) == (1, 1, 1)
    assert _pending(as_coach) == (1, 1, 1)


@pytest.mark.asyncio
async def test_should_leave_pending_counts_null_when_reading_one_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)
    await _request_leave(client, tokens["alice"], "alice", event_id, start)

    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{start}", headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("leaveRequestsPending") is None
    assert body.get("enrollmentRequestsPending") is None
    assert body.get("withdrawalRequestsPending") is None
