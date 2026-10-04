"""The credit statement: order, running balances, paging (#449, R87).

Both entry listings — the member's own ``/mycredits/.../entries`` and the
staff ledger ``/credits/entries`` — can be read newest first, and every entry
carries ``balanceAfter`` (its account's balance after it) and ``totalAfter``
(the member's credit across all accounts after it). Both figures belong to
the entry, so no filter or page changes them.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_member, days_from_now, open_account
from .helpers import create_admin_user
from .test_credit_attendance import mark, programme_with_member

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def my_statement(
    client: AsyncClient, token: str, username: str = "alice", **params: object
) -> dict:
    """The member's statement page, as the member reads it."""
    response = await client.get(
        f"/v1/mycredits/by_id/{username}/entries", params=params, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def ledger(client: AsyncClient, token: str, **params: object) -> dict:
    """The staff ledger page."""
    response = await client.get(
        "/v1/credits/entries", params=params, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def running(items: list[dict]) -> list[tuple[str, int, int, int]]:
    """Each entry as (type, amount, balanceAfter, totalAfter)."""
    return [
        (i["entryType"], i["amount"], i["balanceAfter"], i["totalAfter"]) for i in items
    ]


async def charged_refunded_charged(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, str]:
    """Grant 10, charge 1, refund it (a leave correction), charge 1 again."""
    admin = await create_admin_user(db_session)
    member = await create_member(db_session, "alice")
    account = await open_account(client, admin, "alice", credits=10)
    event_id, occurrence = await programme_with_member(
        client, admin, db_session, "alice"
    )
    charged = await mark(client, admin, event_id, occurrence, "alice", "present")
    assert charged.status_code == 200, charged.text
    cleared = await client.delete(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance/alice",
        headers=auth(admin),
    )
    assert cleared.status_code == 204, cleared.text
    again = await mark(client, admin, event_id, occurrence, "alice", "present")
    assert again.status_code == 200, again.text
    return admin, member, str(account["accountId"])


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R87")
@pytest.mark.requirement("credit:R4")
@pytest.mark.requirement("credit:R44a")
@pytest.mark.requirement("credit:R80")
async def test_should_carry_running_balance_on_each_entry(
    client: AsyncClient, db_session: AsyncSession
):
    """R87: each line says what was left, not only what moved."""
    admin, member, _ = await charged_refunded_charged(client, db_session)

    mine = await my_statement(client, member)
    staff = await ledger(client, admin, membername="alice")

    expected = [
        ("grant", 10, 10, 10),
        ("sessionDeduction", -1, 9, 9),
        ("sessionRefund", 1, 10, 10),
        ("sessionDeduction", -1, 9, 9),
    ]
    assert running(mine["items"]) == expected
    assert running(staff["items"]) == expected


@pytest.mark.asyncio
async def test_should_list_newest_first_when_order_is_desc(
    client: AsyncClient, db_session: AsyncSession
):
    """The statement page wants the latest lines without reading the rest."""
    admin, member, _ = await charged_refunded_charged(client, db_session)

    default = await my_statement(client, member)
    ascending = await my_statement(client, member, order="asc")
    descending = await my_statement(client, member, order="desc")
    staff_desc = await ledger(client, admin, membername="alice", order="desc")

    ids = [i["id"] for i in ascending["items"]]
    assert len(ids) == 4
    assert [i["id"] for i in default["items"]] == ids
    assert [i["id"] for i in descending["items"]] == list(reversed(ids))
    assert [i["id"] for i in staff_desc["items"]] == list(reversed(ids))
    assert descending["items"][0]["balanceAfter"] == 9


@pytest.mark.asyncio
async def test_should_reject_unknown_order(
    client: AsyncClient, db_session: AsyncSession
):
    """Only asc and desc are orders."""
    admin = await create_admin_user(db_session)
    member = await create_member(db_session, "alice")
    # A refused request rolls the shared session back; keep the users.
    await db_session.commit()

    mine = await client.get(
        "/v1/mycredits/by_id/alice/entries",
        params={"order": "sideways"},
        headers=auth(member),
    )
    staff = await client.get(
        "/v1/credits/entries", params={"order": "sideways"}, headers=auth(admin)
    )

    assert mine.status_code == 422
    assert staff.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R87")
async def test_should_move_total_only_by_penalty_when_departure_transfers_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """A transfer moves credit between the member's own accounts.

    The source account runs down to 0 and the new one starts at the
    remainder; the member's total changes only by the penalty.
    """
    admin = await create_admin_user(db_session)
    member = await create_member(db_session, "alice")
    _ = await open_account(client, admin, "alice", credits=5)
    event_id, _ = await programme_with_member(client, admin, db_session, "alice")
    bound = await open_account(client, admin, "alice", credits=10, event_id=event_id)
    removed = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={
            "membernames": ["alice"],
            "creditDisposition": {
                "penalty": 3,
                "validFromUtc": days_from_now(-1),
                "validUntilUtc": days_from_now(90),
                "reason": "Left the programme",
            },
        },
        headers=auth(admin),
    )
    assert removed.status_code == 204, removed.text

    items = (await my_statement(client, member))["items"]

    assert running(items) == [
        ("grant", 5, 5, 5),
        ("grant", 10, 10, 15),
        ("penalty", -3, 7, 12),
        ("transferOut", -7, 0, 12),
        ("transferIn", 7, 7, 12),
    ]
    assert items[3]["accountId"] == bound["accountId"]
    assert items[4]["accountId"] not in (bound["accountId"], items[0]["accountId"])


@pytest.mark.asyncio
async def test_should_keep_running_figures_when_filtered_or_paged(
    client: AsyncClient, db_session: AsyncSession
):
    """An entry's figures are its own, whichever filter or page shows it."""
    admin = await create_admin_user(db_session)
    member = await create_member(db_session, "alice")
    general = await open_account(client, admin, "alice", credits=5)
    event_id, occurrence = await programme_with_member(
        client, admin, db_session, "alice"
    )
    bound = await open_account(client, admin, "alice", credits=4, event_id=event_id)
    charged = await mark(client, admin, event_id, occurrence, "alice", "present")
    assert charged.status_code == 200, charged.text

    full = {i["id"]: i for i in (await my_statement(client, member))["items"]}
    assert len(full) == 3

    views = [
        (await my_statement(client, member, accountId=bound["accountId"]))["items"],
        (await my_statement(client, member, accountId=general["accountId"]))["items"],
        (await my_statement(client, member, eventId=event_id))["items"],
        (await ledger(client, admin, accountId=bound["accountId"]))["items"],
        (await ledger(client, admin, entryType="sessionDeduction"))["items"],
    ]
    for offset in range(3):
        views.append(
            (await my_statement(client, member, offset=offset, limit=1))["items"]
        )
        views.append(
            (await ledger(client, admin, membername="alice", offset=offset, limit=1))[
                "items"
            ]
        )

    seen = 0
    for items in views:
        assert items, "every view here shows at least one entry"
        for item in items:
            seen += 1
            assert item["balanceAfter"] == full[item["id"]]["balanceAfter"]
            assert item["totalAfter"] == full[item["id"]]["totalAfter"]
    # bound 2, general 1, event 2, staff bound 2, deductions 1, pages 3 + 3
    assert seen == 14
    deduction = next(i for i in full.values() if i["entryType"] == "sessionDeduction")
    assert (deduction["balanceAfter"], deduction["totalAfter"]) == (3, 8)


@pytest.mark.asyncio
async def test_should_page_statement_at_its_boundaries(
    client: AsyncClient, db_session: AsyncSession
):
    """total counts every matching entry; pages cut exactly where asked."""
    admin, member, _ = await charged_refunded_charged(client, db_session)
    everything = (await my_statement(client, member))["items"]
    ids = [i["id"] for i in everything]

    first = await my_statement(client, member, offset=0, limit=3)
    last = await my_statement(client, member, offset=3, limit=3)
    beyond = await my_statement(client, member, offset=4, limit=3)
    newest_two = await my_statement(client, member, order="desc", offset=0, limit=2)
    staff_last = await ledger(client, admin, membername="alice", offset=3, limit=3)

    assert [i["id"] for i in first["items"]] == ids[:3]
    assert first["total"] == 4
    assert [i["id"] for i in last["items"]] == ids[3:]
    assert last["total"] == 4
    assert beyond["items"] == []
    assert beyond["total"] == 4
    assert [i["id"] for i in newest_two["items"]] == [ids[3], ids[2]]
    assert [i["id"] for i in staff_last["items"]] == ids[3:]
    assert staff_last["total"] == 4
