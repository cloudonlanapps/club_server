"""Opening, reading and searching credit accounts (#294).

Covers the account-shape invariants (R1-R12), authorization (R13-R17),
and the opening rules (R18-R24) from
``docs/credit_system_requirements.md``. The lifecycle operations
(extend / reverse / transfer) live in ``test_credit_lifecycle.py``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    create_event,
    create_member,
    days_from_now,
    open_account,
)
from club_server.db.models.user import UserStatus

from .helpers import create_admin_user, create_user_with_status

pytestmark = pytest.mark.usefixtures("credit_enabled")


# --- Opening: the happy paths (R18, R2, R3) ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R1")
@pytest.mark.requirement("credit:R2")
@pytest.mark.requirement("credit:R18")
async def test_should_open_general_account_when_no_event_given(
    client: AsyncClient, db_session: AsyncSession
):
    """R2/R18: an account opened without an event is general."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    account = await open_account(client, admin_token, "alice", credits=10)

    assert account["kind"] == "general"
    assert account["eventId"] is None
    assert account["balance"] == 10
    assert account["membername"] == "alice"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R2")
@pytest.mark.requirement("credit:R8")
@pytest.mark.requirement("credit:R18")
async def test_should_open_event_bound_account_when_programme_given(
    client: AsyncClient, db_session: AsyncSession
):
    """R2/R18: naming a programme binds the account to it."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)

    account = await open_account(
        client, admin_token, "alice", credits=8, event_id=event_id
    )

    assert account["kind"] == "event"
    assert account["eventId"] == event_id
    assert account["balance"] == 8


@pytest.mark.asyncio
async def test_should_record_opening_admin_when_account_opened(
    client: AsyncClient, db_session: AsyncSession
):
    """R24/R82: the account records who opened it."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    account = await open_account(client, admin_token, "alice")

    assert account["openedBy"] == "admin"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R5")
async def test_should_issue_eight_character_code_when_account_opened(
    client: AsyncClient, db_session: AsyncSession
):
    """R5: the account id is an 8-character letter-and-digit code."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    account = await open_account(client, admin_token, "alice")

    account_id = account["accountId"]
    assert isinstance(account_id, str)
    assert len(account_id) == 8
    assert account_id.isalnum()


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R5")
async def test_should_issue_distinct_codes_when_two_accounts_opened(
    client: AsyncClient, db_session: AsyncSession
):
    """R5: codes are unique across accounts."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    first = await open_account(client, admin_token, "alice")
    second = await open_account(client, admin_token, "alice")

    assert first["accountId"] != second["accountId"]


# --- Opening: rejections (R6, R19, R23) ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R6")
async def test_should_reject_account_when_event_is_a_camp(
    client: AsyncClient, db_session: AsyncSession
):
    """R6: only programmes consume credit."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    camp_id = await create_event(client, admin_token, event_type="camp")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "eventId": camp_id,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CREDIT_NOT_APPLICABLE"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R6")
async def test_should_reject_account_when_event_is_one_off(
    client: AsyncClient, db_session: AsyncSession
):
    """R6: one-off events never reach the credit subsystem."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    one_off_id = await create_event(client, admin_token, event_type="oneOff")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "eventId": one_off_id,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CREDIT_NOT_APPLICABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("credits", [0, -5])
@pytest.mark.requirement("credit:R19")
async def test_should_reject_account_when_credits_not_positive(
    client: AsyncClient, db_session: AsyncSession, credits: int
):
    """R19: the opening amount must be a positive whole number."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": credits,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_CREDIT_AMOUNT"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R23")
async def test_should_reject_account_when_window_ends_before_it_starts(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: the validity window's end must be after its start."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(30),
            "validUntilUtc": days_from_now(10),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_VALIDITY_WINDOW"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R23")
async def test_should_reject_account_when_window_wholly_in_the_past(
    client: AsyncClient, db_session: AsyncSession
):
    """R23: a window that has already closed cannot be opened."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(-60),
            "validUntilUtc": days_from_now(-30),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_VALIDITY_WINDOW"


@pytest.mark.asyncio
async def test_should_reject_account_when_reason_missing(
    client: AsyncClient, db_session: AsyncSession
):
    """R60: no unexplained movement of credit."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R18a")
@pytest.mark.parametrize(
    "user_status",
    [
        UserStatus.registered,
        UserStatus.pending,
        UserStatus.blocked,
        UserStatus.left,
    ],
)
async def test_should_open_account_when_user_is_not_active(
    client: AsyncClient, db_session: AsyncSession, user_status: UserStatus
):
    """R18a: account status is not a credit gate.

    A payment can be recorded while a new member's registration is still
    under review, or for someone blocked or gone; enrollment and attendance
    keep their own gates.
    """
    admin_token = await create_admin_user(db_session)
    _ = await create_user_with_status(db_session, "alice", user_status)

    opened = await open_account(client, admin_token, "alice", credits=6)

    assert opened["membername"] == "alice"
    assert opened["balance"] == 6
    listed = await client.get(
        "/v1/credits/accounts",
        params={"membername": "alice"},
        headers=auth(admin_token),
    )
    assert listed.status_code == 200, listed.text
    assert [(a["accountId"], a["balance"]) for a in listed.json()["items"]] == [
        (opened["accountId"], 6)
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R18a")
async def test_should_reject_account_when_member_does_not_exist(
    client: AsyncClient, db_session: AsyncSession
):
    """An account cannot be opened for a member who is not there."""
    admin_token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "ghost",
            "credits": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


# --- Several accounts, no top-ups (R9, R20, R22) ---


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R9")
@pytest.mark.requirement("credit:R22")
async def test_should_allow_second_account_when_member_already_has_one_for_event(
    client: AsyncClient, db_session: AsyncSession
):
    """R9/R22: a second account for the same programme is the normal top-up."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)

    first = await open_account(
        client, admin_token, "alice", credits=5, event_id=event_id
    )
    second = await open_account(
        client, admin_token, "alice", credits=5, event_id=event_id
    )

    assert first["accountId"] != second["accountId"]
    assert first["balance"] == 5
    assert second["balance"] == 5


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R20")
async def test_should_not_expose_a_top_up_operation(
    client: AsyncClient, db_session: AsyncSession
):
    """R20: there is no recharge, renew, or add-credits endpoint."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=5)
    account_id = account["accountId"]

    for path in ("add", "topup", "recharge", "renew"):
        response = await client.post(
            f"/v1/credits/accounts/{account_id}/{path}",
            json={"credits": 5, "reason": "more"},
            headers=auth(admin_token),
        )
        assert response.status_code == 404, f"/{path} must not exist"
