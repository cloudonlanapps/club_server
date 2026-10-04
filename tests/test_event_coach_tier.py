"""The event-coach permission tier (#247).

A coach assigned to an event, who is not its organizer, may mark
attendance and decide leave on that event, and nothing more: enrollment,
editing and credit stay where they were. "Assigned" is read from the
schedule's coach rows (#386), so a coach who is not on the event is
refused exactly as before, and the organizer loses nothing.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    auth,
    create_programme,
    create_venue,
    mark,
)

FORBIDDEN = "INSUFFICIENT_PERMISSION"


async def _staffed_programme(
    client: AsyncClient, db_session: AsyncSession, *, minutes: int | None = None
) -> tuple[dict[str, str], int, int]:
    """A programme organized by ``org`` and coached by ``asst``, with ``alice``
    enrolled and ``other`` a coach who is on nothing. Starts in ``minutes`` when
    given (so the register is open), else in two days (so leave can be declared)."""
    tokens = {"admin": await create_admin_user(db_session)}
    for coach in ("org", "asst", "other"):
        tokens[coach] = await create_coach_user(db_session, coach)
    tokens["alice"] = await create_member_user(db_session, "alice")
    venue = await create_venue(client, tokens["admin"])
    start = at(minutes=minutes) if minutes is not None else at(days=2)
    event = await create_programme(
        client,
        tokens["admin"],
        venue,
        start=start,
        organizerName="org",
        coachNames=["asst"],
    )
    assigned = await assign(client, tokens["admin"], event["id"], "alice")
    assert assigned.status_code in (200, 204), assigned.text
    await db_session.commit()
    return tokens, event["id"], start


async def _register(client: AsyncClient, token: str, event_id: int, slot: int):
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/attendance",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return {row["membername"]: row["status"] for row in response.json()}


async def _request_leave(client: AsyncClient, tokens, event_id: int, slot: int):
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(tokens["alice"]),
    )
    assert requested.status_code == 204, requested.text


# --- attendance: the tier ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7")
async def test_should_allow_assigned_coach_to_mark_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session, minutes=20)

    marked = await mark(client, tokens["asst"], event_id, slot, "alice")
    assert marked.status_code == 200, marked.text
    assert marked.json()["marked"] == [{"membername": "alice", "status": "present"}]

    assert await _register(client, tokens["admin"], event_id, slot) == {
        "alice": "present"
    }
    own = await client.get(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/attendance",
        headers=auth(tokens["alice"]),
    )
    assert own.status_code == 200, own.text
    assert own.json()["status"] == "present"


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7")
async def test_should_allow_assigned_coach_to_clear_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session, minutes=20)
    marked = await mark(client, tokens["admin"], event_id, slot, "alice")
    assert marked.status_code == 200, marked.text

    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/attendance/alice",
        headers=auth(tokens["asst"]),
    )
    assert cleared.status_code == 204, cleared.text

    assert await _register(client, tokens["admin"], event_id, slot) == {}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7a")
async def test_should_allow_assigned_coach_to_approve_leave(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    await _request_leave(client, tokens, event_id, slot)

    approved = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/approve",
        json={"membernames": ["alice"]},
        headers=auth(tokens["asst"]),
    )
    assert approved.status_code in (200, 204), approved.text

    assert await _register(client, tokens["admin"], event_id, slot) == {
        "alice": "onLeave"
    }


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7a")
async def test_should_allow_assigned_coach_to_reject_leave(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    await _request_leave(client, tokens, event_id, slot)

    rejected = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/reject",
        json={"membernames": ["alice"], "reason": "Needed on ice"},
        headers=auth(tokens["asst"]),
    )
    assert rejected.status_code in (200, 204), rejected.text

    assert await _register(client, tokens["admin"], event_id, slot) == {}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7")
async def test_should_forbid_unassigned_coach_from_attendance(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session, minutes=20)

    marked = await mark(client, tokens["other"], event_id, slot, "alice")
    assert marked.status_code == 403, marked.text
    assert marked.json()["detail"]["code"] == FORBIDDEN

    assert await _register(client, tokens["admin"], event_id, slot) == {}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7a")
async def test_should_forbid_unassigned_coach_from_leave_decisions(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    await _request_leave(client, tokens, event_id, slot)

    for verb, body in (
        ("approve", {"membernames": ["alice"]}),
        ("reject", {"membernames": ["alice"], "reason": "No"}),
    ):
        response = await client.post(
            f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/{verb}",
            json=body,
            headers=auth(tokens["other"]),
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == FORBIDDEN

    assert await _register(client, tokens["admin"], event_id, slot) == {
        "alice": "onLeaveRequested"
    }


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7b")
async def test_should_keep_organizer_powers_when_coaches_are_assigned(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session, minutes=20)

    marked = await mark(client, tokens["org"], event_id, slot, "alice", "late")
    assert marked.status_code == 200, marked.text

    assert await _register(client, tokens["admin"], event_id, slot) == {"alice": "late"}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7")
async def test_should_stop_recognising_coach_when_unassigned_by_split(
    client: AsyncClient, db_session: AsyncSession
):
    """The tier reads the current schedule: a split that drops the coach
    drops the permission with it."""
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    _ = await create_coach_user(db_session, "replacement")
    await db_session.commit()
    response = await client.patch(
        f"/v1/events/by_id/{event_id}/future",
        json={
            "effectiveDateTimeUtc": slot,
            "coachNames": ["replacement"],
            "version": 1,
        },
        headers=auth(tokens["admin"]),
    )
    assert response.status_code == 200, response.text
    await _request_leave(client, tokens, event_id, slot)

    refused = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/leave/approve",
        json={"membernames": ["alice"]},
        headers=auth(tokens["asst"]),
    )
    assert refused.status_code == 403, refused.text

    assert await _register(client, tokens["admin"], event_id, slot) == {
        "alice": "onLeaveRequested"
    }


# --- everything else stays organizer-or-admin --------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7b")
async def test_should_forbid_assigned_coach_from_every_enrollment_endpoint(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    _ = await create_member_user(db_session, "bob")
    await db_session.commit()

    for verb, body in (
        ("invite", {"membernames": ["bob"]}),
        ("assign", {"membernames": ["bob"]}),
        ("assign-trial", {"membername": "bob"}),
        ("approve", {"membernames": ["bob"]}),
        ("reject", {"membernames": ["bob"]}),
        ("remove", {"membernames": ["alice"]}),
        ("approve-withdraw", {"membernames": ["alice"]}),
        ("reject-withdraw", {"membernames": ["alice"]}),
    ):
        response = await client.post(
            f"/v1/events/by_id/{event_id}/enrollments/{verb}",
            json=body,
            headers=auth(tokens["asst"]),
        )
        assert response.status_code == 403, f"{verb}: {response.text}"
        assert response.json()["detail"]["code"] == FORBIDDEN

    listed = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments", headers=auth(tokens["admin"])
    )
    assert listed.status_code == 200
    assert listed.json()["enrollments"] == {"alice": "assigned"}


@pytest.mark.asyncio
@pytest.mark.requirement("attendance:R7b")
async def test_should_forbid_assigned_coach_from_editing_the_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)

    corrected = await client.patch(
        f"/v1/events/by_id/{event_id}/correction",
        json={"title": "Hijacked", "version": 1},
        headers=auth(tokens["asst"]),
    )
    assert corrected.status_code == 403, corrected.text
    split = await client.patch(
        f"/v1/events/by_id/{event_id}/future",
        json={
            "effectiveDateTimeUtc": slot,
            "coachNames": ["asst", "other"],
            "version": 1,
        },
        headers=auth(tokens["asst"]),
    )
    assert split.status_code == 403, split.text

    fetched = await client.get(
        f"/v1/events/by_id/{event_id}", headers=auth(tokens["admin"])
    )
    assert fetched.json()["title"] != "Hijacked"
    assert fetched.json()["coachNames"] == ["asst"]
