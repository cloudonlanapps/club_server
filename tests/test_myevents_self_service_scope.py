"""Who may act for a member on the self-service enrollment routes (#460).

The member themself, an admin, and the event's organizer or assigned coaches
may accept or decline an invitation, request to join, request or cancel a
withdrawal, and declare or cancel leave on the member's behalf. A coach with
no link to the event is refused with 403, and the refusal changes nothing.
Every action is audited with the acting user.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    assign,
    at,
    audit_rows,
    auth,
    create_programme,
    create_venue,
)

FORBIDDEN = "INSUFFICIENT_PERMISSION"


async def _staffed_programme(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, str], int, int]:
    """A programme organized by ``org`` and coached by ``asst``; ``other`` is a
    coach on nothing. ``alice`` and ``bob`` are members, neither enrolled."""
    tokens = {"admin": await create_admin_user(db_session)}
    for coach in ("org", "asst", "other"):
        tokens[coach] = await create_coach_user(db_session, coach)
    for member in ("alice", "bob"):
        tokens[member] = await create_member_user(db_session, member)
    venue = await create_venue(client, tokens["admin"])
    start = at(days=2)
    event = await create_programme(
        client,
        tokens["admin"],
        venue,
        start=start,
        organizerName="org",
        coachNames=["asst"],
        visibility="public",
    )
    await db_session.commit()
    return tokens, event["id"], start


async def _invite(client: AsyncClient, tokens, event_id: int, member: str) -> None:
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": [member]},
        headers=auth(tokens["admin"]),
    )
    assert response.status_code in (200, 204), response.text


async def _enroll(client: AsyncClient, tokens, event_id: int, member: str) -> None:
    response = await assign(client, tokens["admin"], event_id, member)
    assert response.status_code in (200, 204), response.text


async def _status(client: AsyncClient, tokens, event_id: int, member: str):
    response = await client.get(
        f"/v1/myevents/by_id/{member}/{event_id}/enrollments",
        headers=auth(tokens[member]),
    )
    if response.status_code == 404:
        return None
    assert response.status_code == 200, response.text
    return response.json()["status"]


async def _attendance(client: AsyncClient, tokens, event_id: int, slot: int):
    response = await client.get(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/attendance",
        headers=auth(tokens["alice"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["status"] if body else None


def _assert_forbidden(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == FORBIDDEN


# --- the unrelated coach is refused ----------------------------------------


@pytest.mark.asyncio
async def test_should_refuse_accept_invite_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _invite(client, tokens, event_id, "alice")
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _status(client, tokens, event_id, "alice") == "invited"


@pytest.mark.asyncio
async def test_should_refuse_decline_invite_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _invite(client, tokens, event_id, "alice")
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/decline",
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _status(client, tokens, event_id, "alice") == "invited"


@pytest.mark.asyncio
async def test_should_refuse_request_enrollment_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)

    response = await client.post(
        f"/v1/myevents/by_id/bob/{event_id}/enrollments/request",
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _status(client, tokens, event_id, "bob") is None


@pytest.mark.asyncio
async def test_should_refuse_withdrawal_request_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _enroll(client, tokens, event_id, "alice")
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _status(client, tokens, event_id, "alice") == "assigned"


@pytest.mark.asyncio
async def test_should_refuse_cancel_withdrawal_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _enroll(client, tokens, event_id, "alice")
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(tokens["alice"]),
    )
    assert requested.status_code == 204, requested.text
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/cancel-withdraw",
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _status(client, tokens, event_id, "alice") == "withdrawRequested"


@pytest.mark.asyncio
async def test_should_refuse_leave_request_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    await _enroll(client, tokens, event_id, "alice")
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _attendance(client, tokens, event_id, slot) is None


@pytest.mark.asyncio
async def test_should_refuse_leave_cancel_when_coach_not_on_event(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    await _enroll(client, tokens, event_id, "alice")
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(tokens["alice"]),
    )
    assert requested.status_code == 204, requested.text
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/cancel",
        headers=auth(tokens["other"]),
    )
    _assert_forbidden(response)
    assert await _attendance(client, tokens, event_id, slot) == "onLeaveRequested"


@pytest.mark.asyncio
async def test_should_refuse_self_service_when_member_acts_for_another_member(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _invite(client, tokens, event_id, "alice")
    await db_session.commit()

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers=auth(tokens["bob"]),
    )
    _assert_forbidden(response)
    assert await _status(client, tokens, event_id, "alice") == "invited"


# --- the member, admins and the event's staff may act ------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["alice", "admin", "org", "asst"])
async def test_should_accept_invite_when_actor_is_member_admin_or_event_staff(
    client: AsyncClient, db_session: AsyncSession, actor: str
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _invite(client, tokens, event_id, "alice")

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/accept",
        headers=auth(tokens[actor]),
    )
    assert response.status_code == 204, response.text
    assert await _status(client, tokens, event_id, "alice") == "accepted"

    rows = await audit_rows(db_session, "enrollment_accepted")
    assert [(r.actor_username, r.target_username) for r in rows] == [(actor, "alice")]


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["org", "asst"])
async def test_should_declare_leave_when_actor_is_event_staff(
    client: AsyncClient, db_session: AsyncSession, actor: str
):
    tokens, event_id, slot = await _staffed_programme(client, db_session)
    await _enroll(client, tokens, event_id, "alice")

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/occurrences/{slot}/leave/request",
        json={"reason": "Away"},
        headers=auth(tokens[actor]),
    )
    assert response.status_code == 204, response.text
    assert await _attendance(client, tokens, event_id, slot) == "onLeaveRequested"

    rows = await audit_rows(db_session, "leave_requested")
    assert [(r.actor_username, r.target_username) for r in rows] == [(actor, "alice")]


@pytest.mark.asyncio
async def test_should_request_withdrawal_when_actor_is_event_coach(
    client: AsyncClient, db_session: AsyncSession
):
    tokens, event_id, _ = await _staffed_programme(client, db_session)
    await _enroll(client, tokens, event_id, "alice")

    response = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(tokens["asst"]),
    )
    assert response.status_code == 204, response.text
    assert await _status(client, tokens, event_id, "alice") == "withdrawRequested"

    rows = await audit_rows(db_session, "withdrawal_requested")
    assert [(r.actor_username, r.target_username) for r in rows] == [("asst", "alice")]
