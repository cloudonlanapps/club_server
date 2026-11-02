"""The staff occurrence list carries each session's attendance counts (#142).

``GET /events/occurrences`` gives every occurrence ``attendanceExpected``
(the members whose enrollment covers it) and ``attendanceMarked`` (how many
of those have a recorded status: present, absent, late or on leave). A
pending leave request is not marked. The counts for the whole range come
from a fixed number of queries.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.engine.interfaces import DBAPICursor
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from .enrollment_rejoin_helpers import remove
from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    backdate_enrollment,
    create_programme,
    create_venue,
    list_occurrences,
    mark,
)

pytestmark = pytest.mark.requirement("attendance:R38a")


async def _programme(
    client: AsyncClient, db_session: AsyncSession, *, start: int
) -> tuple[str, dict[str, str], int]:
    """A daily programme starting at ``start`` with alice and bob assigned."""
    admin = await create_admin_user(db_session)
    tokens = {
        "alice": await create_member_user(db_session, "alice"),
        "bob": await create_member_user(db_session, "bob"),
    }
    await db_session.commit()
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, start=start)
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


def _counts(occurrence: dict[str, Any]) -> tuple[int, int]:
    return occurrence["attendanceExpected"], occurrence["attendanceMarked"]


async def _request_leave(
    client: AsyncClient, token: str, member: str, event_id: int, slot: int
) -> None:
    response = await client.post(
        f"/v1/myevents/by_id/{member}/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(token),
    )
    assert response.status_code == 204, response.text


@pytest.mark.asyncio
async def test_should_count_nothing_marked_when_no_attendance_is_recorded(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)

    occurrence = await _occurrence(client, admin, event_id, start)

    assert _counts(occurrence) == (2, 0)


@pytest.mark.asyncio
async def test_should_give_a_coach_the_same_counts_when_listing_occurrences(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)
    coach = await create_coach_user(db_session)
    await db_session.commit()
    marked = await mark(client, admin, event_id, start, "alice")
    assert marked.status_code == 200, marked.text

    as_admin = await _occurrence(client, admin, event_id, start)
    as_coach = await _occurrence(client, coach, event_id, start)

    assert _counts(as_admin) == (2, 1)
    assert _counts(as_coach) == (2, 1)


@pytest.mark.asyncio
async def test_should_count_partly_marked_when_some_members_are_marked(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)

    marked = await mark(client, admin, event_id, start, "alice", "late")
    assert marked.status_code == 200, marked.text

    expected, done = _counts(await _occurrence(client, admin, event_id, start))
    assert (expected, done) == (2, 1)
    assert 0 < done < expected


@pytest.mark.asyncio
async def test_should_count_fully_marked_when_every_member_is_marked(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)

    for member, status in (("alice", "present"), ("bob", "absent")):
        marked = await mark(client, admin, event_id, start, member, status)
        assert marked.status_code == 200, marked.text

    assert _counts(await _occurrence(client, admin, event_id, start)) == (2, 2)


@pytest.mark.asyncio
async def test_should_count_only_the_marked_session_when_the_next_one_is_listed(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)
    marked = await mark(client, admin, event_id, start, "alice")
    assert marked.status_code == 200, marked.text

    next_day = await _occurrence(client, admin, event_id, start + DAY_MS)

    assert _counts(next_day) == (2, 0)


@pytest.mark.asyncio
async def test_should_not_count_as_marked_when_leave_is_only_requested(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)

    await _request_leave(client, tokens["alice"], "alice", event_id, start)

    assert _counts(await _occurrence(client, admin, event_id, start)) == (2, 0)


@pytest.mark.asyncio
async def test_should_count_as_marked_when_leave_is_approved(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, tokens, event_id = await _programme(client, db_session, start=start)
    for member in ("alice", "bob"):
        await _request_leave(client, tokens[member], member, event_id, start)

    approved = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/leave/approve",
        json={"membernames": ["alice"]},
        headers=auth(admin),
    )
    assert approved.status_code in (200, 204), approved.text

    assert _counts(await _occurrence(client, admin, event_id, start)) == (2, 1)


@pytest.mark.asyncio
async def test_should_leave_out_a_member_when_their_enrollment_does_not_cover_it(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)
    marked = await mark(client, admin, event_id, start, "alice")
    assert marked.status_code == 200, marked.text
    assert _counts(await _occurrence(client, admin, event_id, start)) == (2, 1)

    removed = await remove(client, admin, event_id, "alice")
    assert removed.status_code == 204, removed.text

    assert _counts(await _occurrence(client, admin, event_id, start)) == (1, 0)


@pytest.mark.asyncio
async def test_should_count_members_marked_in_a_past_session_they_enrolled_after(
    client: AsyncClient, db_session: AsyncSession
):
    """#146: the register shows both marked, so the counts say so too."""
    start = at(days=-2)
    admin, _, event_id = await _programme(client, db_session, start=start)
    for member, status in (("alice", "present"), ("bob", "absent")):
        marked = await mark(client, admin, event_id, start, member, status)
        assert marked.status_code == 200, marked.text

    assert _counts(await _occurrence(client, admin, event_id, start)) == (2, 2)


@pytest.mark.asyncio
async def test_should_count_a_covered_unmarked_member_beside_a_marked_later_one(
    client: AsyncClient, db_session: AsyncSession
):
    """#146: bob, enrolled before the past session and unmarked, is still
    expected; alice, enrolled after it and marked, counts as marked."""
    start = at(days=-2)
    admin, _, event_id = await _programme(client, db_session, start=start)
    await backdate_enrollment(db_session, event_id, "bob", start - HOUR_MS)
    marked = await mark(client, admin, event_id, start, "alice")
    assert marked.status_code == 200, marked.text

    assert _counts(await _occurrence(client, admin, event_id, start)) == (2, 1)


@pytest_asyncio.fixture
async def statements(test_engine: AsyncEngine) -> AsyncGenerator[list[str], None]:
    """Every SQL statement run while the test is in progress."""
    seen: list[str] = []

    def _record(
        _conn: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _params: object,
        _context: ExecutionContext | None,
        _many: bool,
    ) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(test_engine.sync_engine, "before_cursor_execute", _record)
    yield seen
    event.remove(test_engine.sync_engine, "before_cursor_execute", _record)


def _count_queries(seen: list[str]) -> int:
    return sum(
        1
        for sql in seen
        if sql.startswith("SELECT")
        and ("FROM attendance_records" in sql or "FROM enrollments" in sql)
    )


@pytest.mark.asyncio
async def test_should_count_in_a_fixed_number_of_queries_when_the_range_grows(
    client: AsyncClient, db_session: AsyncSession, statements: list[str]
):
    start = at(minutes=20)
    admin, _, event_id = await _programme(client, db_session, start=start)
    later = start + 120 * MINUTE_MS
    second = await create_programme(
        client, admin, await create_venue(client, admin, "Second rink"), start=later
    )
    assigned = await assign(client, admin, second["id"], "alice")
    assert assigned.status_code in (200, 204), assigned.text

    statements.clear()
    one_day = await list_occurrences(
        client, admin, start - MINUTE_MS, later + MINUTE_MS
    )
    one_day_queries = _count_queries(statements)

    statements.clear()
    week = await list_occurrences(client, admin, start - MINUTE_MS, start + 7 * DAY_MS)
    week_queries = _count_queries(statements)

    assert len(one_day) == 2
    assert len(week) > len(one_day)
    assert {o["eventId"]: _counts(o) for o in one_day} == {
        event_id: (2, 0),
        second["id"]: (1, 0),
    }
    assert one_day_queries == week_queries
    assert week_queries <= 2
