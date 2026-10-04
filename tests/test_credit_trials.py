"""Trial credit (#294, R50-R53).

A trial is funded by an event-bound account marked as a trial. Trials are
not free, trial and ordinary credit never mix in either direction, and a
member whose trial credit runs out is removed from the programme rather
than blocked — the only case where running out of credit ends an
enrollment.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, balance_of, create_member, open_account
from .helpers import create_admin_user
from .test_attendance import create_venue, future_time_ms, move_event_to_recent_past

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def programme(client: AsyncClient, admin_token: str) -> int:
    """A programme starting tomorrow."""
    venue_id = await create_venue(client, admin_token)
    start = future_time_ms(24)
    response = await client.post(
        "/v1/events",
        json={
            "title": "Trial Programme",
            "type": "programme",
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": start + 60 * 60 * 1000,
        },
        headers=auth(admin_token),
    )
    return response.json()["id"]


async def enrollment_status(
    client: AsyncClient, admin_token: str, event_id: int, username: str
) -> str | None:
    """The member's current enrollment status on the event."""
    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments", headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text
    return response.json()["enrollments"].get(username)


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R50")
async def test_should_assign_trial_when_member_holds_trial_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R50: a trial is funded by a trial account."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await programme(client, admin_token)
    _ = await open_account(
        client, admin_token, "alice", credits=2, event_id=event_id, is_trial=True
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "alice"},
        headers=auth(admin_token),
    )

    assert response.status_code in (200, 204), response.text
    assert await enrollment_status(client, admin_token, event_id, "alice") == (
        "assignedTrial"
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R50")
@pytest.mark.requirement("credit:R53")
async def test_should_refuse_trial_assignment_when_only_ordinary_credit_held(
    client: AsyncClient, db_session: AsyncSession
):
    """R53: an ordinary account never funds a trial.

    The trial flag is not on the enrollment row yet at this point, so a
    naive implementation would check ordinary accounts and let the member
    onto a trial they cannot fund.
    """
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await programme(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=50)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "alice"},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CREDIT"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R53")
async def test_should_refuse_ordinary_assignment_when_only_trial_credit_held(
    client: AsyncClient, db_session: AsyncSession
):
    """R53: and a trial account never funds an ordinary enrollment."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await programme(client, admin_token)
    _ = await open_account(
        client, admin_token, "alice", credits=50, event_id=event_id, is_trial=True
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CREDIT"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R51")
async def test_should_deduct_from_trial_account_when_trial_member_marked(
    client: AsyncClient, db_session: AsyncSession
):
    """R51: trial credit is spent by attendance like any other."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await programme(client, admin_token)
    trial = await open_account(
        client, admin_token, "alice", credits=3, event_id=event_id, is_trial=True
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "alice"},
        headers=auth(admin_token),
    )
    occurrence = await move_event_to_recent_past(db_session, event_id, ["alice"])

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert marked.status_code == 200, marked.text
    assert await balance_of(client, admin_token, trial["accountId"]) == 2


@pytest.mark.asyncio
async def test_should_remove_member_when_trial_credit_exhausted(
    client: AsyncClient, db_session: AsyncSession
):
    """R52: an exhausted trial ends the enrollment rather than blocking it.

    The only case in the subsystem where running out of credit removes
    someone, so it is worth pinning: a completed trial is over, not paused
    waiting for a top-up.
    """
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await programme(client, admin_token)
    trial = await open_account(
        client, admin_token, "alice", credits=1, event_id=event_id, is_trial=True
    )
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "alice"},
        headers=auth(admin_token),
    )
    occurrence = await move_event_to_recent_past(db_session, event_id, ["alice"])

    marked = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert marked.status_code == 200, marked.text
    assert marked.json()["trialEnded"] == [{"membername": "alice"}]
    assert await balance_of(client, admin_token, trial["accountId"]) == 0
    assert await enrollment_status(client, admin_token, event_id, "alice") == "removed"
    mine = await client.get(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments", headers=auth(admin_token)
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["withdrawnAtUtc"] is not None
    assert mine.json()["withdrawalReason"] == "trialCreditExhausted"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R53")
async def test_should_not_rescue_exhausted_trial_from_general_account(
    client: AsyncClient, db_session: AsyncSession
):
    """R53: exhaustion means exhaustion, even holding other usable credit."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await programme(client, admin_token)
    trial = await open_account(
        client, admin_token, "alice", credits=1, event_id=event_id, is_trial=True
    )
    general = await open_account(client, admin_token, "alice", credits=20)
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": "alice"},
        headers=auth(admin_token),
    )
    occurrence = await move_event_to_recent_past(db_session, event_id, ["alice"])

    _ = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert await balance_of(client, admin_token, trial["accountId"]) == 0
    assert await balance_of(client, admin_token, general["accountId"]) == 20
    assert await enrollment_status(client, admin_token, event_id, "alice") == "removed"
