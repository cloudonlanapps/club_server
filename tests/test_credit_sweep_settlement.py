"""The credit sweep settles every due departure, whatever one row does (#485).

A deferred disposition's window is checked when the admin states it; the
sweep applies it as stated, even once that window has closed (credit R74a).
Each row settles in its own savepoint, so one that fails is logged and left
pending while the rest of the sweep goes ahead (credit R73d).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.enrollment import Enrollment
from club_server.schemas.credit import CreditDispositionRequest
from club_server.services.credit_lifecycle import CreditLifecycleService
from club_server.services.credit_pending import pending_disposition_json
from club_server.services.credit_sweep import sweep_credit_settlements

from .credit_helpers import balance_of, open_account
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    backdate_enrollment,
    create_programme,
    create_venue,
)

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def _programme_under_way(
    client: AsyncClient, db_session: AsyncSession, members: list[str]
) -> tuple[str, int, int, dict[str, str]]:
    """A programme with a slot under way, each of ``members`` holding 10 bound credits.

    Returns ``(admin token, programme id, slot, {member: bound account id})``.
    """
    admin = await create_admin_user(db_session)
    for member in members:
        _ = await create_member_user(db_session, member)
    venue = await create_venue(client, admin)
    slot = at(minutes=-30)
    programme = await create_programme(
        client, admin, venue, start=slot, end=slot + 2 * HOUR_MS
    )
    bound: dict[str, str] = {}
    for member in members:
        account = await open_account(
            client, admin, member, credits=10, event_id=programme["id"]
        )
        bound[member] = str(account["accountId"])
        assigned = await assign(client, admin, programme["id"], member)
        assert assigned.status_code == 204, assigned.text
        await backdate_enrollment(db_session, programme["id"], member, slot - HOUR_MS)
    return admin, programme["id"], slot, bound


async def _remove(
    client: AsyncClient, admin: str, programme_id: int, members: list[str]
) -> None:
    removed = await client.post(
        f"/v1/events/by_id/{programme_id}/enrollments/remove",
        json={
            "membernames": members,
            "creditDisposition": {
                "penalty": 0,
                "validFromUtc": at(days=-1),
                "validUntilUtc": at(days=90),
                "reason": "Leaving",
            },
        },
        headers=auth(admin),
    )
    assert removed.status_code == 204, removed.text


async def _enrollment(
    db_session: AsyncSession, programme_id: int, member: str
) -> Enrollment:
    return (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == programme_id, Enrollment.membername == member
            )
        )
    ).scalar_one()


async def _general_accounts(client: AsyncClient, admin: str, member: str) -> list[dict]:
    response = await client.get(
        "/v1/credits/accounts", params={"membername": member}, headers=auth(admin)
    )
    assert response.status_code == 200, response.text
    return [a for a in response.json()["items"] if a["kind"] == "general"]


@pytest.mark.requirement("credit:R74a")
@pytest.mark.asyncio
async def test_should_settle_deferred_departure_when_its_window_has_lapsed(
    client: AsyncClient, db_session: AsyncSession
):
    admin, programme_id, slot, bound = await _programme_under_way(
        client, db_session, ["skater"]
    )
    await _remove(client, admin, programme_id, ["skater"])
    # The window was open when stated and has closed by the time the sweep
    # settles it.
    lapsed_from, lapsed_until = at(days=-30), at(days=-1)
    enrollment = await _enrollment(db_session, programme_id, "skater")
    enrollment.pending_disposition = pending_disposition_json(
        CreditDispositionRequest.model_construct(
            penalty=0,
            valid_from_utc=lapsed_from,
            valid_until_utc=lapsed_until,
            reason="Leaving",
        ),
        "admin",
    )
    await db_session.commit()

    settled = await sweep_credit_settlements(db_session, slot + 2 * HOUR_MS + MINUTE_MS)
    await db_session.commit()

    assert settled == 1
    assert await balance_of(client, admin, bound["skater"]) == 0
    general = await _general_accounts(client, admin, "skater")
    assert [(a["balance"], a["validUntilUtc"]) for a in general] == [(10, lapsed_until)]
    enrollment = await _enrollment(db_session, programme_id, "skater")
    await db_session.refresh(enrollment)
    assert enrollment.pending_disposition is None


@pytest.mark.requirement("credit:R73d")
@pytest.mark.asyncio
async def test_should_settle_other_departures_when_one_row_fails(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin, programme_id, slot, bound = await _programme_under_way(
        client, db_session, ["skater", "goalie"]
    )
    await _remove(client, admin, programme_id, ["skater", "goalie"])
    await db_session.commit()
    original = CreditLifecycleService.dispose_bound_credit

    async def _fail_for_goalie(self, **kwargs):
        if kwargs["membername"] == "goalie":
            raise RuntimeError("forced settlement failure")
        return await original(self, **kwargs)

    monkeypatch.setattr(
        CreditLifecycleService, "dispose_bound_credit", _fail_for_goalie
    )
    due = slot + 2 * HOUR_MS + MINUTE_MS

    settled = await sweep_credit_settlements(db_session, due)
    await db_session.commit()

    assert settled == 1
    assert await balance_of(client, admin, bound["skater"]) == 0
    assert [a["balance"] for a in await _general_accounts(client, admin, "skater")] == [
        10
    ]
    assert await balance_of(client, admin, bound["goalie"]) == 10
    assert await _general_accounts(client, admin, "goalie") == []
    goalie = await _enrollment(db_session, programme_id, "goalie")
    await db_session.refresh(goalie)
    assert goalie.pending_disposition is not None

    # The failed row stays due, and the next sweep settles it.
    monkeypatch.setattr(CreditLifecycleService, "dispose_bound_credit", original)
    assert await sweep_credit_settlements(db_session, due + HOUR_MS) == 1
    await db_session.commit()
    assert await balance_of(client, admin, bound["goalie"]) == 0
