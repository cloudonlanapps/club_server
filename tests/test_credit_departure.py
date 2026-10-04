"""Resolving a member's programme credit when they leave (#294).

Covers R71, R73 and R98: a member cannot be removed while credit sits
unresolved in an account bound to the programme, approving a withdrawal
carries the same disposition, and a disposition sent to a deployment that
does not run on credits is rejected rather than ignored.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    balance_of,
    create_event,
    create_member,
    days_from_now,
    get_account,
    open_account,
)
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


def disposition(penalty: int) -> dict[str, object]:
    """A credit disposition with a flat penalty."""
    return {
        "penalty": penalty,
        "validFromUtc": days_from_now(-1),
        "validUntilUtc": days_from_now(90),
        "reason": "Left the programme",
    }


async def enrolled_member(
    client: AsyncClient, admin_token: str, username: str, credits: int
) -> tuple[int, str]:
    """Assign ``username`` to a programme with a bound account of ``credits``."""
    event_id = await create_event(client, admin_token)
    account = await open_account(
        client, admin_token, username, credits=credits, event_id=event_id
    )
    assign = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert assign.status_code in (200, 204), assign.text
    return event_id, str(account["accountId"])


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R71")
async def test_should_refuse_removal_when_credit_unresolved(
    client: AsyncClient, db_session: AsyncSession
):
    """R71: a member cannot be removed with credit left in a bound account."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 20)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CREDIT_DISPOSITION_REQUIRED"
    assert await balance_of(client, admin_token, account_id) == 20


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R65")
@pytest.mark.requirement("credit:R66")
@pytest.mark.requirement("credit:R70")
async def test_should_move_remainder_to_general_when_removed_with_penalty(
    client: AsyncClient, db_session: AsyncSession
):
    """R65-R68: balance minus a flat penalty moves to a new general account."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 20)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"], "creditDisposition": disposition(5)},
        headers=auth(admin_token),
    )

    assert response.status_code in (200, 204), response.text
    source = await get_account(client, admin_token, account_id)
    assert source["state"] == "closed"
    accounts = await client.get("/v1/mycredits/by_id/alice", headers=auth(member_token))
    general = [row for row in accounts.json() if row["kind"] == "general"]
    assert len(general) == 1
    assert general[0]["balance"] == 15


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R68")
@pytest.mark.requirement("credit:R70")
async def test_should_open_no_account_when_penalty_takes_everything(
    client: AsyncClient, db_session: AsyncSession
):
    """R68: a penalty that consumes the balance leaves nothing behind."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 10)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"], "creditDisposition": disposition(10)},
        headers=auth(admin_token),
    )

    assert response.status_code in (200, 204), response.text
    assert await balance_of(client, admin_token, account_id) == 0
    accounts = await client.get("/v1/mycredits/by_id/alice", headers=auth(member_token))
    assert [row for row in accounts.json() if row["kind"] == "general"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R66a")
async def test_should_charge_penalty_once_when_member_holds_two_accounts(
    client: AsyncClient, db_session: AsyncSession
):
    """R66: the penalty is one figure for the departure, not one per account.

    Two packages instead of one must not cost the member twice the penalty.
    """
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id, _ = await enrolled_member(client, admin_token, "alice", 10)
    _ = await open_account(client, admin_token, "alice", credits=10, event_id=event_id)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"], "creditDisposition": disposition(5)},
        headers=auth(admin_token),
    )

    assert response.status_code in (200, 204), response.text
    accounts = await client.get("/v1/mycredits/by_id/alice", headers=auth(member_token))
    general = [row for row in accounts.json() if row["kind"] == "general"]
    assert sum(row["balance"] for row in general) == 15


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R65")
@pytest.mark.requirement("credit:R73")
async def test_should_settle_credit_when_withdrawal_approved(
    client: AsyncClient, db_session: AsyncSession
):
    """R73: approving a withdrawal carries the same disposition."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 20)
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member_token),
    )
    assert requested.status_code in (200, 204), requested.text

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["alice"], "creditDisposition": disposition(2)},
        headers=auth(admin_token),
    )

    assert response.status_code in (200, 204), response.text
    source = await get_account(client, admin_token, account_id)
    assert source["state"] == "closed"
    accounts = await client.get("/v1/mycredits/by_id/alice", headers=auth(member_token))
    general = [row for row in accounts.json() if row["kind"] == "general"]
    assert general[0]["balance"] == 18


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R71")
@pytest.mark.requirement("credit:R73")
async def test_should_refuse_withdrawal_approval_when_credit_unresolved(
    client: AsyncClient, db_session: AsyncSession
):
    """R73: the same guard applies to withdrawal as to removal."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id, _ = await enrolled_member(client, admin_token, "alice", 20)
    _ = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member_token),
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CREDIT_DISPOSITION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R71")
async def test_should_remove_without_disposition_when_member_holds_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """A member with nothing bound to the programme needs no disposition."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=5)
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    assert response.status_code in (200, 204), response.text


# ---------------------------------------------------------------------------
# #476: a disposition's validity window is checked when it is stated
# ---------------------------------------------------------------------------


def _window(valid_from: int, valid_until: int) -> dict[str, object]:
    return {
        "penalty": 0,
        "validFromUtc": valid_from,
        "validUntilUtc": valid_until,
        "reason": "Left the programme",
    }


def _is_schema_validation_error(body: dict) -> bool:
    """FastAPI's request-validation shape, which clients read as VALIDATION_ERROR."""
    return isinstance(body.get("detail"), list) and bool(body["detail"])


@pytest.mark.asyncio
async def test_should_reject_removal_when_disposition_window_ends_before_it_starts(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 20)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={
            "membernames": ["alice"],
            "creditDisposition": _window(days_from_now(30), days_from_now(10)),
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422, response.text
    assert _is_schema_validation_error(response.json())
    source = await get_account(client, admin_token, account_id)
    assert source["state"] != "closed"
    assert source["balance"] == 20


@pytest.mark.asyncio
async def test_should_reject_removal_when_disposition_window_has_already_closed(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 20)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={
            "membernames": ["alice"],
            "creditDisposition": _window(days_from_now(-30), days_from_now(-1)),
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422, response.text
    assert _is_schema_validation_error(response.json())
    assert await balance_of(client, admin_token, account_id) == 20


@pytest.mark.asyncio
async def test_should_reject_withdrawal_approval_when_disposition_window_is_empty(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id, account_id = await enrolled_member(client, admin_token, "alice", 20)
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member_token),
    )
    assert requested.status_code in (200, 204), requested.text
    same = days_from_now(30)

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["alice"], "creditDisposition": _window(same, same)},
        headers=auth(admin_token),
    )

    assert response.status_code == 422, response.text
    assert _is_schema_validation_error(response.json())
    source = await get_account(client, admin_token, account_id)
    assert source["state"] != "closed"
    assert source["balance"] == 20
