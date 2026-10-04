"""Credit standing across a programme's roster (#294, R90).

The one endpoint where an event id belongs in the path: who on this
programme can be marked on Saturday, and whose credit is about to lapse.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import (
    auth,
    create_event,
    create_member,
    days_from_now,
    drain_account,
    expire_account,
    open_account,
)
from .helpers import create_admin_user, create_coach_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def roster(
    client: AsyncClient, token: str, event_id: int, **params: object
) -> list[dict[str, object]]:
    """The credit roster for one programme."""
    response = await client.get(
        f"/v1/events/by_id/{event_id}/credits", params=params, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def assigned(
    client: AsyncClient, admin_token: str, event_id: int, username: str
) -> None:
    """Put ``username`` on the programme."""
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert response.status_code in (200, 204), response.text


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R39")
@pytest.mark.requirement("credit:R90")
async def test_should_report_usable_credit_for_each_enrolled_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R90: the roster answers who can be marked."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    await create_member(db_session, "bob")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=7)
    _ = await open_account(client, admin_token, "bob", credits=3)
    await assigned(client, admin_token, event_id, "alice")
    await assigned(client, admin_token, event_id, "bob")

    rows = await roster(client, admin_token, event_id)

    by_member = {row["membername"]: row for row in rows}
    assert by_member["alice"]["usableCredits"] == 7
    assert by_member["bob"]["usableCredits"] == 3
    assert by_member["alice"]["blocked"] is False


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R25")
async def test_should_name_the_account_that_would_pay(
    client: AsyncClient, db_session: AsyncSession
):
    """R25/R26: the roster shows the event-bound account, not the general one."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=20)
    bound = await open_account(
        client, admin_token, "alice", credits=4, event_id=event_id
    )
    await assigned(client, admin_token, event_id, "alice")

    rows = await roster(client, admin_token, event_id)

    assert rows[0]["payingAccountId"] == bound["accountId"]
    assert rows[0]["usableCredits"] == 24


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R42")
async def test_should_flag_member_as_blocked_when_credit_exhausted(
    client: AsyncClient, db_session: AsyncSession
):
    """R42: blocked is derived from the balance, not stored."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    account = await open_account(client, admin_token, "alice", credits=1)
    await assigned(client, admin_token, event_id, "alice")
    await drain_account(client, admin_token, account["accountId"])

    rows = await roster(client, admin_token, event_id)

    assert rows[0]["blocked"] is True
    assert rows[0]["usableCredits"] == 0
    assert rows[0]["payingAccountId"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R90")
async def test_should_list_only_blocked_members_when_state_filtered(
    client: AsyncClient, db_session: AsyncSession
):
    """R90: the admin wants the short list, not the whole roster."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    await create_member(db_session, "bob")
    event_id = await create_event(client, admin_token)
    alice_account = await open_account(client, admin_token, "alice", credits=1)
    _ = await open_account(client, admin_token, "bob", credits=5)
    await assigned(client, admin_token, event_id, "alice")
    await assigned(client, admin_token, event_id, "bob")
    await drain_account(client, admin_token, alice_account["accountId"])

    rows = await roster(client, admin_token, event_id, state="blocked")

    assert [row["membername"] for row in rows] == ["alice"]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R90")
async def test_should_report_next_expiry_for_each_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R90: whose credit lapses next, so it can be chased before it does."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    expires = days_from_now(14)
    _ = await open_account(client, admin_token, "alice", credits=5, valid_until=expires)
    await assigned(client, admin_token, event_id, "alice")

    rows = await roster(client, admin_token, event_id)

    assert rows[0]["nextExpiryUtc"] == expires


@pytest.mark.asyncio
async def test_should_exclude_members_who_are_only_invited(
    client: AsyncClient, db_session: AsyncSession
):
    """The roster is who could be marked, not who was asked."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=5)
    invited = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )
    assert invited.status_code in (200, 204), invited.text

    rows = await roster(client, admin_token, event_id)

    assert rows == []


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R72a")
async def test_should_report_bound_credit_when_bound_account_has_expired(
    client: AsyncClient, db_session: AsyncSession
):
    """R72a: an expired package cannot pay, yet removal still has to resolve it.

    The roster tells the two figures apart, so a client knows a removal needs
    a disposition before it asks for one.
    """
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    bound = await open_account(
        client, admin_token, "alice", credits=5, event_id=event_id
    )
    await assigned(client, admin_token, event_id, "alice")
    await expire_account(db_session, str(bound["accountId"]))

    rows = await roster(client, admin_token, event_id)

    assert len(rows) == 1
    assert rows[0]["usableCredits"] == 0
    assert rows[0]["blocked"] is True
    assert rows[0]["boundCredits"] == 5


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R71")
async def test_should_report_no_bound_credit_when_member_holds_only_general_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R71: general credit is markable but never needs a disposition."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=3)
    await assigned(client, admin_token, event_id, "alice")

    rows = await roster(client, admin_token, event_id)

    assert rows[0]["usableCredits"] == 3
    assert rows[0]["blocked"] is False
    assert rows[0]["boundCredits"] == 0


@pytest.mark.asyncio
async def test_should_count_only_programme_bound_credit_when_member_holds_both(
    client: AsyncClient, db_session: AsyncSession
):
    """boundCredits is the programme's share; general credit stays out of it."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=20)
    _ = await open_account(client, admin_token, "alice", credits=4, event_id=event_id)
    await assigned(client, admin_token, event_id, "alice")

    rows = await roster(client, admin_token, event_id)

    assert rows[0]["usableCredits"] == 24
    assert rows[0]["boundCredits"] == 4


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R74")
async def test_should_include_member_when_withdrawal_requested(
    client: AsyncClient, db_session: AsyncSession
):
    """R74: a pending withdrawal leaves the member enrolled and chargeable."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    await create_member(db_session, "bob")
    event_id = await create_event(client, admin_token)
    _ = await open_account(client, admin_token, "alice", credits=6, event_id=event_id)
    _ = await open_account(client, admin_token, "bob", credits=2)
    await assigned(client, admin_token, event_id, "alice")
    await assigned(client, admin_token, event_id, "bob")
    requested = await client.post(
        f"/v1/myevents/by_id/alice/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member_token),
    )
    assert requested.status_code in (200, 204), requested.text

    rows = await roster(client, admin_token, event_id)

    by_member = {row["membername"]: row for row in rows}
    assert sorted(str(name) for name in by_member) == ["alice", "bob"]
    assert by_member["alice"]["usableCredits"] == 6
    assert by_member["alice"]["boundCredits"] == 6
    assert by_member["bob"]["boundCredits"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R6")
async def test_should_reject_roster_when_event_is_not_a_programme(
    client: AsyncClient, db_session: AsyncSession
):
    """R6: camps never reach the credit subsystem."""
    admin_token = await create_admin_user(db_session)
    camp_id = await create_event(client, admin_token, event_type="camp")

    response = await client.get(
        f"/v1/events/by_id/{camp_id}/credits", headers=auth(admin_token)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CREDIT_NOT_APPLICABLE"


@pytest.mark.asyncio
async def test_should_allow_coach_to_read_the_roster(
    client: AsyncClient, db_session: AsyncSession
):
    """R16: staff read credit; the coach taking the register needs this."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)
    event_id = await create_event(client, admin_token)

    response = await client.get(
        f"/v1/events/by_id/{event_id}/credits", headers=auth(coach_token)
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R15")
async def test_should_reject_roster_when_caller_is_a_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R15: a member reads their own credit, not everyone else's."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)

    response = await client.get(
        f"/v1/events/by_id/{event_id}/credits", headers=auth(member_token)
    )

    assert response.status_code == 403
