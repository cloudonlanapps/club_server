"""Enrollment, attendance and credit on a programme (#384 phase 0).

Programme R27–R29f and R32a–R32e, enrollment R48b, credit R74a: the
per-occurrence cancelled predicate, departures that settle at the last
covered occurrence, and trials bounded by money.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services.credit_sweep import sweep_credit_settlements
from club_server.services.scheduler import scan_pending_marks

from .credit_helpers import balance_of, open_account
from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    auth,
    backdate_enrollment,
    cancel_occurrence,
    create_programme,
    create_venue,
    enrollment_of,
    list_occurrences,
    list_user_occurrences,
    mark,
    notifications_for,
    set_cutoff,
)


async def _accounts_of(client: AsyncClient, token: str, member: str) -> list[dict]:
    response = await client.get(
        "/v1/credits/accounts", params={"membername": member}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _disposition() -> dict[str, object]:
    return {
        "penalty": 0,
        "validFromUtc": at(days=-1),
        "validUntilUtc": at(days=90),
        "reason": "Leaving",
    }


# ---------------------------------------------------------------------------
# R27, R28: cancelled is per occurrence, and every subsystem agrees
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R27")
@pytest.mark.asyncio
async def test_should_refuse_attendance_when_slot_is_at_or_after_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-4)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    await set_cutoff(db_session, programme["id"], start + 2 * DAY_MS)

    before = await mark(client, admin, programme["id"], start + DAY_MS, "skater")
    at_cutoff = await mark(client, admin, programme["id"], start + 2 * DAY_MS, "skater")

    assert before.status_code == 200, before.text
    assert at_cutoff.status_code == 422, at_cutoff.text
    assert at_cutoff.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.requirement("programme:R27")
@pytest.mark.asyncio
async def test_should_refuse_attendance_when_occurrence_carries_cancelled_override(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-4)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    slot = start + DAY_MS
    assert (
        await cancel_occurrence(client, admin, programme["id"], slot)
    ).status_code == 204

    response = await mark(client, admin, programme["id"], slot, "skater")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R28")
@pytest.mark.asyncio
async def test_should_give_same_answer_everywhere_when_occurrence_is_cancelled_by_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-4)
    programme = await create_programme(client, admin, venue, start=start)
    account = await open_account(
        client, admin, "skater", credits=5, event_id=programme["id"]
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    slot = start + 3 * DAY_MS
    await set_cutoff(db_session, programme["id"], slot)

    refused = await mark(client, admin, programme["id"], slot, "skater")
    listed = await list_occurrences(
        client, admin, slot - HOUR_MS, slot + HOUR_MS, event_id=programme["id"]
    )
    _ = await scan_pending_marks(db_session, at(days=1))
    await db_session.commit()
    reminders_for_slot = [
        n
        for n in await notifications_for(
            db_session, "admin", "attendance.pending_mark_reminder"
        )
        if n.payload["data"]["occurrenceTimeUtc"] == slot
    ]

    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "CANCELLED_OCCURRENCE"
    assert [o["status"] for o in listed] == ["cancelled"]
    assert reminders_for_slot == []
    assert await balance_of(client, admin, str(account["accountId"])) == 5


# ---------------------------------------------------------------------------
# R29b: a departure ends the enrollment
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R29b")
@pytest.mark.asyncio
async def test_should_end_enrollment_when_member_is_removed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/remove",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, programme["id"], "skater") == "removed"
    mine = await list_user_occurrences(
        client,
        member,
        "skater",
        start - HOUR_MS,
        start + 3 * DAY_MS,
        event_id=programme["id"],
    )
    assert mine == []


# ---------------------------------------------------------------------------
# R29c–R29f, enrollment R48b, credit R74a: deferred settlement
# ---------------------------------------------------------------------------


async def _member_leaving_mid_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int, int, str]:
    """A programme with an occurrence under way and a member who just left it.

    Returns ``(admin token, programme id, slot under way, bound account id)``.
    The member is removed with a zero-penalty disposition while the slot is
    still running, so their credit cannot be settled yet.
    """
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    slot = at(minutes=-30)
    programme = await create_programme(
        client, admin, venue, start=slot, end=slot + 2 * HOUR_MS
    )
    account = await open_account(
        client, admin, "skater", credits=10, event_id=programme["id"]
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", slot - HOUR_MS)
    removed = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/remove",
        json={"membernames": ["skater"], "creditDisposition": _disposition()},
        headers=auth(admin),
    )
    assert removed.status_code == 204, removed.text
    return admin, programme["id"], slot, str(account["accountId"])


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R29c")
@pytest.mark.requirement("credit:R74a")
@pytest.mark.asyncio
async def test_should_keep_balance_bound_when_departing_member_still_has_a_covered_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin, programme_id, _, bound = await _member_leaving_mid_occurrence(
        client, db_session
    )

    assert await enrollment_of(client, admin, programme_id, "skater") == "removed"
    assert await balance_of(client, admin, bound) == 10
    assert [a["kind"] for a in await _accounts_of(client, admin, "skater")] == ["event"]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R29d")
@pytest.mark.requirement("enrollment:R48b")
@pytest.mark.asyncio
async def test_should_charge_covered_occurrence_then_settle_remainder_when_it_has_passed(
    client: AsyncClient, db_session: AsyncSession
):
    admin, programme_id, slot, bound = await _member_leaving_mid_occurrence(
        client, db_session
    )

    marked = await mark(client, admin, programme_id, slot, "skater")
    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []
    assert await balance_of(client, admin, bound) == 9

    await sweep_credit_settlements(db_session, slot + 2 * HOUR_MS + MINUTE_MS)
    await db_session.commit()

    assert await balance_of(client, admin, bound) == 0
    general = [
        a for a in await _accounts_of(client, admin, "skater") if a["kind"] == "general"
    ]
    assert [a["balance"] for a in general] == [9]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R82")
@pytest.mark.asyncio
async def test_should_record_removing_admin_when_deferred_settlement_applies(
    client: AsyncClient, db_session: AsyncSession
):
    """R82: the sweep applies the admin's decision, so the admin is its actor."""
    admin, _, slot, bound = await _member_leaving_mid_occurrence(client, db_session)

    await sweep_credit_settlements(db_session, slot + 2 * HOUR_MS + MINUTE_MS)
    await db_session.commit()

    general = [
        a for a in await _accounts_of(client, admin, "skater") if a["kind"] == "general"
    ]
    assert len(general) == 1
    assert general[0]["openedBy"] == "admin"
    entries = await client.get(
        "/v1/credits/entries", params={"membername": "skater"}, headers=auth(admin)
    )
    assert entries.status_code == 200, entries.text
    settlement = [
        (row["entryType"], row["accountId"], row["actorUsername"])
        for row in entries.json()["items"]
        if row["entryType"] in ("penalty", "transferOut", "transferIn")
    ]
    assert settlement == [
        ("transferOut", bound, "admin"),
        ("transferIn", general[0]["accountId"], "admin"),
    ]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R74a")
@pytest.mark.asyncio
async def test_should_charge_general_credit_once_when_earlier_session_marked_after_settlement(
    client: AsyncClient, db_session: AsyncSession
):
    """R74a: a session before the departure, marked late, is paid exactly once.

    The member left with nothing under way, so their bound balance moved to
    a general account at once. A session they were on two days earlier is
    marked only now, inside the edit window: it is still covered, and it is
    paid from general credit by the normal selection order (R25), which
    includes the account the transfer created. Nothing is re-settled.
    """
    admin = await create_admin_user(db_session)
    regular_admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-3, hours=-3)
    programme = await create_programme(client, admin, venue, start=start)
    bound = await open_account(
        client, admin, "skater", credits=10, event_id=programme["id"]
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    removed = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/remove",
        json={"membernames": ["skater"], "creditDisposition": _disposition()},
        headers=auth(admin),
    )
    assert removed.status_code == 204, removed.text
    general = [
        a for a in await _accounts_of(client, admin, "skater") if a["kind"] == "general"
    ]
    assert [a["balance"] for a in general] == [10]

    marked = await mark(
        client, regular_admin, programme["id"], start + 2 * DAY_MS, "skater"
    )

    assert marked.status_code == 200, marked.text
    assert marked.json()["refused"] == []
    assert await balance_of(client, admin, str(general[0]["accountId"])) == 9
    assert await balance_of(client, admin, str(bound["accountId"])) == 0
    entries = await client.get(
        "/v1/credits/entries",
        params={"membername": "skater", "entryType": "sessionDeduction"},
        headers=auth(admin),
    )
    assert entries.status_code == 200, entries.text
    deductions = entries.json()["items"]
    assert [(d["accountId"], d["amount"]) for d in deductions] == [
        (general[0]["accountId"], -1)
    ]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R29e")
@pytest.mark.asyncio
async def test_should_settle_immediately_when_no_covered_occurrence_remains(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, start=at(days=2))
    account = await open_account(
        client, admin, "skater", credits=10, event_id=programme["id"]
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/remove",
        json={"membernames": ["skater"], "creditDisposition": _disposition()},
        headers=auth(admin),
    )

    assert response.status_code == 204, response.text
    assert await balance_of(client, admin, str(account["accountId"])) == 0
    general = [
        a for a in await _accounts_of(client, admin, "skater") if a["kind"] == "general"
    ]
    assert [a["balance"] for a in general] == [10]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R29f")
@pytest.mark.asyncio
async def test_should_settle_through_sweep_not_on_read_and_only_once(
    client: AsyncClient, db_session: AsyncSession
):
    admin, _, slot, bound = await _member_leaving_mid_occurrence(client, db_session)
    after = slot + 2 * HOUR_MS + MINUTE_MS

    still_bound = await balance_of(client, admin, bound)
    await sweep_credit_settlements(db_session, slot + HOUR_MS)
    await db_session.commit()
    too_early = await balance_of(client, admin, bound)
    await sweep_credit_settlements(db_session, after)
    await db_session.commit()
    await sweep_credit_settlements(db_session, after + HOUR_MS)
    await db_session.commit()

    assert (still_bound, too_early) == (10, 10)
    accounts = await _accounts_of(client, admin, "skater")
    assert sorted(a["kind"] for a in accounts) == ["event", "general"]
    assert sum(a["balance"] for a in accounts) == 10


# ---------------------------------------------------------------------------
# R32a–R32e: trials
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R32a")
@pytest.mark.asyncio
async def test_should_assign_trial_when_event_is_a_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign-trial",
        json={"membername": "skater"},
        headers=auth(admin),
    )

    assert response.status_code == 204, response.text
    assert (
        await enrollment_of(client, admin, programme["id"], "skater") == "assignedTrial"
    )


@pytest.mark.requirement("programme:R32b")
@pytest.mark.asyncio
async def test_should_reject_trial_when_member_is_ineligible(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater", gender="male")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, gender="female")

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign-trial",
        json={"membername": "skater"},
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"
    assert await enrollment_of(client, admin, programme["id"], "skater") is None


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R32d")
@pytest.mark.asyncio
async def test_should_end_trial_when_trial_credit_is_spent(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-3)
    programme = await create_programme(client, admin, venue, start=start)
    _ = await open_account(
        client, admin, "skater", credits=2, event_id=programme["id"], is_trial=True
    )
    trial = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign-trial",
        json={"membername": "skater"},
        headers=auth(admin),
    )
    assert trial.status_code == 204, trial.text
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)

    first = await mark(client, admin, programme["id"], start, "skater")
    assert first.status_code == 200 and first.json()["refused"] == [], first.text
    assert (
        await enrollment_of(client, admin, programme["id"], "skater") == "assignedTrial"
    )
    second = await mark(client, admin, programme["id"], start + DAY_MS, "skater")
    assert second.status_code == 200 and second.json()["refused"] == [], second.text

    assert await enrollment_of(client, admin, programme["id"], "skater") == "removed"


@pytest.mark.requirement("programme:R32e")
@pytest.mark.asyncio
async def test_should_leave_trial_unbounded_when_deployment_has_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-3)
    programme = await create_programme(client, admin, venue, start=start)
    trial = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign-trial",
        json={"membername": "skater"},
        headers=auth(admin),
    )
    assert trial.status_code == 204, trial.text
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)

    for day in range(3):
        marked = await mark(
            client, admin, programme["id"], start + day * DAY_MS, "skater"
        )
        assert marked.status_code == 200, marked.text

    assert (
        await enrollment_of(client, admin, programme["id"], "skater") == "assignedTrial"
    )
    assert (
        await notifications_for(db_session, "skater", "enrollment.cancelled_admin")
        == []
    )
