"""Shared fixtures and helpers for the credit-system tests (#294).

Kept out of ``helpers.py`` so the credit surface can be read in one place
while it is being built. Everything here drives the public HTTP API; the
only direct database use is moving an event into the past, which has no
endpoint.
"""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_member_user

DAY_MS = 24 * 60 * 60 * 1000


def now_ms() -> int:
    """Current time in epoch milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def days_from_now(days: int) -> int:
    """Epoch milliseconds ``days`` from now; negative for the past."""
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)


def auth(token: str) -> dict[str, str]:
    """Authorization header for ``token``."""
    return {"Authorization": f"Bearer {token}"}


async def create_venue(client: AsyncClient, token: str) -> int:
    """Create a venue and return its id."""
    response = await client.post(
        "/v1/venues", json={"name": "Credit Test Venue"}, headers=auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def create_event(
    client: AsyncClient,
    admin_token: str,
    *,
    event_type: str = "programme",
    title: str = "Credit Test Programme",
    start_in_days: int = 1,
) -> int:
    """Create an event of ``event_type`` and return its id."""
    venue_id = await create_venue(client, admin_token)
    start = days_from_now(start_in_days)
    response = await client.post(
        "/v1/events",
        json={
            "title": title,
            "type": event_type,
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": start + 60 * 60 * 1000,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def create_member(db_session: AsyncSession, username: str = "member") -> str:
    """Create an active member and return their access token."""
    return await create_member_user(db_session, username)


async def open_account(
    client: AsyncClient,
    admin_token: str,
    membername: str,
    *,
    credits: int = 10,
    event_id: int | None = None,
    is_trial: bool = False,
    valid_from: int | None = None,
    valid_until: int | None = None,
    reason: str = "Package purchased",
) -> dict[str, object]:
    """Open a credit account and return the response body.

    Asserts a 201 so a caller setting up state fails loudly rather than
    carrying a broken account into the assertions that follow.
    """
    body: dict[str, object] = {
        "membername": membername,
        "credits": credits,
        "validFromUtc": valid_from if valid_from is not None else days_from_now(-1),
        "validUntilUtc": valid_until if valid_until is not None else days_from_now(90),
        "isTrial": is_trial,
        "reason": reason,
    }
    if event_id is not None:
        body["eventId"] = event_id
    response = await client.post(
        "/v1/credits/accounts", json=body, headers=auth(admin_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def get_account(
    client: AsyncClient, token: str, account_id: str
) -> dict[str, object]:
    """Fetch one account by its code."""
    response = await client.get(
        f"/v1/credits/accounts/{account_id}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def balance_of(client: AsyncClient, token: str, account_id: str) -> int:
    """Current balance of one account."""
    account = await get_account(client, token, account_id)
    return int(account["balance"])  # pyright: ignore[reportArgumentType]


async def entries_for(
    client: AsyncClient, token: str, **filters: object
) -> list[dict[str, object]]:
    """Ledger entries matching ``filters``."""
    response = await client.get(
        "/v1/credits/entries", params=filters, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def expire_account(
    db_session: AsyncSession, account_id: str, *, days_ago: int = 1
) -> None:
    """Age an account so its validity window has closed.

    There is deliberately no endpoint for this: opening a window that has
    already closed is rejected (R23), and an extension only moves the end
    date forwards (R62). Tests that need an expired account therefore age
    one in place, the same way the attendance tests move an event into the
    past.
    """
    from sqlalchemy import select

    from club_server.db.models.credit_account import CreditAccount

    result = await db_session.execute(
        select(CreditAccount).where(CreditAccount.code == account_id)
    )
    account = result.scalar_one()
    account.valid_from = days_from_now(-90)
    account.valid_until = days_from_now(-days_ago)
    await db_session.flush()


async def drain_account(client: AsyncClient, admin_token: str, account_id: str) -> None:
    """Empty an account by reversing its grant.

    A member cannot be *assigned* to a programme without at least one usable
    credit (R35), so the "enrolled but out of credit" state cannot be built
    directly — it is reached the way it is reached in real life: the member
    joins with credit and later runs out.
    """
    response = await client.post(
        f"/v1/credits/accounts/{account_id}/reverse",
        json={"reason": "Draining for test setup"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
