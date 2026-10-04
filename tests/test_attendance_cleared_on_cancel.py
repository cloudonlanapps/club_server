"""Cancelling an occurrence clears its register (#337; attendance R21b–R21d).

A session that never happened must not keep rows saying members were
present. Cancelling deletes the ``present`` / ``absent`` / ``late`` rows for
that occurrence — after any credit has been refunded, since the refund
finds whom to repay through those very rows. Leave rows are member-initiated
with their own approval lifecycle and are kept. A restored occurrence is
unmarked; staff mark the register again if the session goes ahead.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.enrollment import Enrollment
from club_server.db.models.event import Event

from .credit_helpers import balance_of, open_account
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    auth,
    cancel_occurrence,
    cancel_series,
    create_camp,
    create_programme,
    create_venue,
    mark,
    occurrence_version,
    version_of,
)

MINUTE_MS = 60 * 1000


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


async def _imminent_programme(
    client: AsyncClient,
    db_session: AsyncSession,
    *members: str,
    minutes: int = 20,
    credits: int | None = None,
) -> tuple[str, int, int, dict | None]:
    """A programme starting in ``minutes``: markable now, cancellable by super-admin.

    With ``credits`` the first member gets an account before being assigned,
    since a credit deployment refuses to enrol a member holding none.
    """
    admin = await create_admin_user(db_session)
    for member in members:
        _ = await create_member_user(db_session, member)
    venue = await create_venue(client, admin)
    start = at(minutes=minutes)
    event = await create_programme(client, admin, venue, start=start)
    account = None
    if credits is not None:
        account = await open_account(client, admin, members[0], credits=credits)
    assigned = await assign(client, admin, event["id"], *members)
    assert assigned.status_code in (200, 204), assigned.text
    return admin, event["id"], start, account


async def _past_camp(
    client: AsyncClient, db_session: AsyncSession, *, days: int, days_ago: int
) -> tuple[str, int, list[int]]:
    """A daily camp of ``days`` whose first day was ``days_ago`` days ago.

    Returns the admin token, the event id and the slot times in order. The
    camp is created ahead and then moved back in the database, since no
    endpoint creates one in the past; slot times are whole seconds.
    """
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    event = await create_camp(client, admin, venue, count=days)
    assigned = await assign(client, admin, event["id"], "alice")
    assert assigned.status_code in (200, 204), assigned.text
    first = at(days=-days_ago, hours=-1)
    row = (
        await db_session.execute(select(Event).where(Event.id == event["id"]))
    ).scalar_one()
    row.start_time = first
    row.end_time = first + 60 * MINUTE_MS
    enrollment = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event["id"], Enrollment.membername == "alice"
            )
        )
    ).scalar_one()
    enrollment.enrolled_at = first - 1
    await db_session.commit()
    db_session.expire_all()
    return admin, event["id"], [first + i * 24 * 60 * MINUTE_MS for i in range(days)]


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21b")
async def test_should_delete_marks_when_occurrence_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, slot, _ = await _imminent_programme(
        client, db_session, "alice", "bob", "carol"
    )
    for member, status in (("alice", "present"), ("bob", "absent"), ("carol", "late")):
        marked = await mark(client, admin, event_id, slot, member, status)
        assert marked.status_code == 200, marked.text
    assert await _register(client, admin, event_id, slot) == {
        "alice": "present",
        "bob": "absent",
        "carol": "late",
    }

    cancelled = await cancel_occurrence(client, admin, event_id, slot)
    assert cancelled.status_code == 204, cancelled.text

    assert await _register(client, admin, event_id, slot) == {}
    assert await _member_records(client, admin, "alice") == []


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21b")
async def test_should_keep_leave_rows_when_occurrence_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    venue = await create_venue(client, admin)
    start = at(days=2)
    event = await create_programme(client, admin, venue, start=start)
    event_id = event["id"]
    assigned = await assign(client, admin, event_id, "alice", "bob")
    assert assigned.status_code in (200, 204), assigned.text
    for member, token in (("alice", alice), ("bob", bob)):
        requested = await client.post(
            f"/v1/myevents/by_id/{member}/{event_id}/occurrences/{start}/leave/request",
            json={"reason": "Away"},
            headers=auth(token),
        )
        assert requested.status_code == 204, requested.text
    approved = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{start}/leave/approve",
        json={"membernames": ["alice"]},
        headers=auth(admin),
    )
    assert approved.status_code in (200, 204), approved.text
    assert await _register(client, admin, event_id, start) == {
        "alice": "onLeave",
        "bob": "onLeaveRequested",
    }

    cancelled = await cancel_occurrence(client, admin, event_id, start)
    assert cancelled.status_code == 204, cancelled.text

    assert await _register(client, admin, event_id, start) == {
        "alice": "onLeave",
        "bob": "onLeaveRequested",
    }
    assert [r["status"] for r in await _member_records(client, alice, "alice")] == [
        "onLeave"
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21b")
async def test_should_leave_other_occurrences_marked_when_one_is_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    """Only the cancelled occurrence's register goes; the day before keeps its rows."""
    admin, event_id, (day1, day2, _) = await _past_camp(
        client, db_session, days=3, days_ago=1
    )
    for slot in (day1, day2):
        marked = await mark(client, admin, event_id, slot, "alice")
        assert marked.status_code == 200, marked.text

    cancelled = await cancel_occurrence(client, admin, event_id, day2)
    assert cancelled.status_code == 204, cancelled.text

    assert await _register(client, admin, event_id, day1) == {"alice": "present"}
    assert await _register(client, admin, event_id, day2) == {}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21b")
@pytest.mark.usefixtures("credit_enabled")
async def test_should_refund_before_deleting_marks_when_occurrence_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    """The refund finds whom to repay through the rows, so it runs first (R48)."""
    admin, event_id, slot, account = await _imminent_programme(
        client, db_session, "alice", credits=10
    )
    assert account is not None
    marked = await mark(client, admin, event_id, slot, "alice")
    assert marked.status_code == 200, marked.text
    assert await balance_of(client, admin, account["accountId"]) == 9

    cancelled = await cancel_occurrence(client, admin, event_id, slot)
    assert cancelled.status_code == 204, cancelled.text

    assert await balance_of(client, admin, account["accountId"]) == 10
    assert await _register(client, admin, event_id, slot) == {}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21c")
async def test_should_delete_marks_at_or_after_cutoff_when_series_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    """A super-admin may cancel a camp from a day already marked; those rows go,
    the day before the cutoff keeps its register."""
    admin, event_id, (day1, day2, day3, _) = await _past_camp(
        client, db_session, days=4, days_ago=2
    )
    for slot in (day1, day2, day3):
        marked = await mark(client, admin, event_id, slot, "alice")
        assert marked.status_code == 200, marked.text

    cancelled = await cancel_series(client, admin, event_id, day2)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["untilTimeUtc"] == day2

    assert await _register(client, admin, event_id, day1) == {"alice": "present"}
    assert await _register(client, admin, event_id, day2) == {}
    assert await _register(client, admin, event_id, day3) == {}
    assert [
        r["occurrenceTimeUtc"] for r in await _member_records(client, admin, "alice")
    ] == [day1]


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21d")
async def test_should_leave_occurrence_unmarked_when_restored_after_cancel(
    client: AsyncClient, db_session: AsyncSession
):
    admin, event_id, slot, _ = await _imminent_programme(client, db_session, "alice")
    marked = await mark(client, admin, event_id, slot, "alice")
    assert marked.status_code == 200, marked.text
    cancelled = await cancel_occurrence(client, admin, event_id, slot)
    assert cancelled.status_code == 204, cancelled.text

    restored = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/undo-cancel",
        json={"version": await occurrence_version(client, admin, event_id, slot)},
        headers=auth(admin),
    )
    assert restored.status_code == 204, restored.text

    assert await _register(client, admin, event_id, slot) == {}
    remarked = await mark(client, admin, event_id, slot, "alice")
    assert remarked.status_code == 200, remarked.text
    assert await _register(client, admin, event_id, slot) == {"alice": "present"}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21d")
@pytest.mark.usefixtures("credit_enabled")
async def test_should_charge_again_when_remarked_after_restore(
    client: AsyncClient, db_session: AsyncSession
):
    """No surviving row means no claim: a restore charges nothing, the re-mark does."""
    admin, event_id, slot, account = await _imminent_programme(
        client, db_session, "alice", credits=10
    )
    assert account is not None
    _ = await mark(client, admin, event_id, slot, "alice")
    _ = await cancel_occurrence(client, admin, event_id, slot)
    assert await balance_of(client, admin, account["accountId"]) == 10

    restored = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/undo-cancel",
        json={"version": await occurrence_version(client, admin, event_id, slot)},
        headers=auth(admin),
    )
    assert restored.status_code == 204, restored.text
    assert await balance_of(client, admin, account["accountId"]) == 10

    remarked = await mark(client, admin, event_id, slot, "alice")
    assert remarked.status_code == 200, remarked.text
    assert await balance_of(client, admin, account["accountId"]) == 9


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R21b")
async def test_should_allow_reschedule_when_only_marked_occurrence_was_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    """Pins the widening the decision accepted: with the register gone, a camp
    whose only marks were on a cancelled day is reschedulable again — for a
    caller who also resets the cancellation override."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")
    venue = await create_venue(client, admin)
    start = at(minutes=20)
    event = await create_camp(client, admin, venue, start=start, count=3)
    event_id = event["id"]
    assigned = await assign(client, admin, event_id, "alice")
    assert assigned.status_code in (200, 204), assigned.text
    marked = await mark(client, admin, event_id, start, "alice")
    assert marked.status_code == 200, marked.text
    cancelled = await cancel_occurrence(client, admin, event_id, start)
    assert cancelled.status_code == 204, cancelled.text

    new_start = at(days=3)
    rescheduled = await client.post(
        f"/v1/events/by_id/{event_id}/reschedule",
        json={
            "version": await version_of(client, admin, event_id),
            "startTimeUtc": new_start,
            "resetOverrides": True,
        },
        headers=auth(admin),
    )
    assert rescheduled.status_code == 200, rescheduled.text
    assert rescheduled.json()["startTimeUtc"] == new_start
