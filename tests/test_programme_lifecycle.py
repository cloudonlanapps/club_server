"""Programme lifecycle: terminate, extend, extend-indefinitely (#384 phase 0).

Executable form of ``docs/programme_requirements.md`` R1–R10c and R33, and of
the lifecycle rules in ``docs/event_lifecycle_requirements.md`` that decide
what a cutoff means for joins and for credit. Each test names the rule it is
the evidence for; the ones still red carry a strict xfail naming the issue
whose implementation turns them green.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services.credit_sweep import sweep_credit_settlements

from .credit_helpers import balance_of, open_account
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    MINUTE_MS,
    assign,
    at,
    audit_rows,
    auth,
    cancel_series,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    enrollment_of,
    extend,
    extend_indefinitely,
    get_event,
    list_occurrences,
    list_user_occurrences,
    notifications_for,
    occurrence_version,
    set_cutoff,
    split,
    terminate,
)


def _statuses(occurrences: list[dict]) -> dict[int, str]:
    return {o["occurrenceTimeUtc"]: o["status"] for o in occurrences}


# ---------------------------------------------------------------------------
# R1–R4: terminate and its cutoff
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R1")
@pytest.mark.asyncio
async def test_should_terminate_programme_when_admin_supplies_reason_and_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 5 * DAY_MS

    response = await terminate(client, admin, programme["id"], cutoff)

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] == cutoff
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] == cutoff


@pytest.mark.requirement("programme:R1")
@pytest.mark.asyncio
async def test_should_reject_terminate_when_reason_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/terminate",
        json={"cutoffTimeUtc": start + 5 * DAY_MS, "version": 1},
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None


@pytest.mark.requirement("programme:R1")
@pytest.mark.asyncio
async def test_should_allow_terminate_when_caller_is_organizer_coach(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "organiser")
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(
        client, admin, venue, start=start, organizerName="organiser"
    )

    response = await terminate(client, coach, programme["id"], start + 5 * DAY_MS)

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] == start + 5 * DAY_MS


@pytest.mark.requirement("programme:R1")
@pytest.mark.asyncio
async def test_should_forbid_terminate_when_coach_is_not_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "bystander")
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await terminate(client, coach, programme["id"], start + 5 * DAY_MS)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None


@pytest.mark.requirement("programme:R1")
@pytest.mark.asyncio
async def test_should_reject_terminate_when_event_is_a_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    camp = await create_camp(client, admin, venue, start=start)

    response = await terminate(client, admin, camp["id"], start + 2 * DAY_MS)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"


@pytest.mark.requirement("programme:R2")
@pytest.mark.asyncio
async def test_should_reject_terminate_when_cutoff_is_not_an_occurrence_start(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await terminate(
        client, admin, programme["id"], start + 5 * DAY_MS + HOUR_MS
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY"
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None


@pytest.mark.requirement("programme:R2")
@pytest.mark.asyncio
async def test_should_include_named_occurrence_in_termination_when_cutoff_is_its_start(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS

    response = await terminate(client, admin, programme["id"], cutoff)
    assert response.status_code == 200, response.text

    statuses = _statuses(
        await list_occurrences(
            client, admin, start - HOUR_MS, start + 5 * DAY_MS, event_id=programme["id"]
        )
    )
    assert statuses[cutoff - DAY_MS] == "scheduled"
    assert statuses[cutoff] == "cancelled"
    assert statuses[cutoff + DAY_MS] == "cancelled"


@pytest.mark.requirement("programme:R3")
@pytest.mark.asyncio
async def test_should_reject_terminate_when_cutoff_is_within_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(minutes=20)
    programme = await create_programme(client, admin, venue, start=start)

    response = await terminate(client, admin, programme["id"], start)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CUTOFF_TOO_SOON"
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None


@pytest.mark.requirement("programme:R3")
@pytest.mark.asyncio
async def test_should_accept_terminate_when_cutoff_is_beyond_thirty_minutes(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(minutes=20)
    programme = await create_programme(client, admin, venue, start=start)

    response = await terminate(client, admin, programme["id"], start + DAY_MS)

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] == start + DAY_MS


@pytest.mark.requirement("programme:R4")
@pytest.mark.asyncio
async def test_should_terminate_old_programme_when_cutoff_is_beyond_generation_limit(
    client: AsyncClient, db_session: AsyncSession
):
    """A daily programme three years old has over a thousand past occurrences;
    a cutoff is matched against the rule, not found by enumerating them."""
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-1100)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 1103 * DAY_MS

    response = await terminate(client, admin, programme["id"], cutoff)

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] == cutoff


# ---------------------------------------------------------------------------
# R5, L12, L21: a bounded event keeps running to its cutoff
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R5")
@pytest.mark.asyncio
async def test_should_keep_enrollment_open_when_programme_terminated_before_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    assert (
        await terminate(client, super_admin, programme["id"], start + 5 * DAY_MS)
    ).status_code == 200

    response = await assign(client, admin, programme["id"], "skater")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, programme["id"], "skater") == "assigned"
    mine = await list_user_occurrences(
        client, member, "skater", start - HOUR_MS, start + 3 * DAY_MS
    )
    assert [o["status"] for o in mine if o["eventId"] == programme["id"]] == [
        "scheduled",
        "scheduled",
        "scheduled",
    ]


@pytest.mark.requirement("lifecycle:L12")
@pytest.mark.asyncio
async def test_should_block_join_when_no_live_occurrence_remains(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=-10)
    programme = await create_programme(client, super_admin, venue, start=start)
    await set_cutoff(db_session, programme["id"], start + 3 * DAY_MS)

    response = await assign(client, admin, programme["id"], "skater")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    assert await enrollment_of(client, admin, programme["id"], "skater") is None


@pytest.mark.requirement("lifecycle:L12")
@pytest.mark.asyncio
async def test_should_allow_join_when_super_admin_bypasses_ended_event(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=-10)
    programme = await create_programme(client, super_admin, venue, start=start)
    await set_cutoff(db_session, programme["id"], start + 3 * DAY_MS)

    response = await assign(client, super_admin, programme["id"], "skater")

    assert response.status_code == 204, response.text
    assert (
        await enrollment_of(client, super_admin, programme["id"], "skater")
        == "assigned"
    )


@pytest.mark.requirement("lifecycle:L12")
@pytest.mark.asyncio
async def test_should_block_join_when_dropped_oneoff_has_no_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    oneoff = await create_oneoff(client, super_admin, venue)
    assert (await drop(client, super_admin, oneoff["id"])).status_code == 200

    response = await assign(client, admin, oneoff["id"], "skater")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("lifecycle:L12")
@pytest.mark.asyncio
async def test_should_block_join_when_every_remaining_occurrence_is_cancelled(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    camp = await create_camp(client, super_admin, venue, start=start, count=2)
    for slot in (start, start + DAY_MS):
        cancelled = await client.post(
            f"/v1/events/by_id/{camp['id']}/occurrences/{slot}/cancel",
            json={
                "version": await occurrence_version(
                    client, super_admin, camp["id"], slot
                ),
                "reason": "No ice",
            },
            headers=auth(super_admin),
        )
        assert cancelled.status_code == 204, cancelled.text

    response = await assign(client, admin, camp["id"], "skater")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("lifecycle:L21")
@pytest.mark.requirement("camps:R5b")
@pytest.mark.asyncio
async def test_should_keep_enrollment_open_when_camp_cancelled_with_future_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    camp = await create_camp(client, super_admin, venue, start=start, count=5)
    cancelled = await cancel_series(client, super_admin, camp["id"], start + 3 * DAY_MS)
    assert cancelled.status_code == 200, cancelled.text

    response = await assign(client, admin, camp["id"], "skater")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, camp["id"], "skater") == "assigned"


@pytest.mark.requirement("enrollment:R15")
@pytest.mark.asyncio
async def test_should_allow_self_request_when_terminated_programme_still_runs(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(
        client, super_admin, venue, start=start, visibility="public"
    )
    assert (
        await terminate(client, super_admin, programme["id"], start + 5 * DAY_MS)
    ).status_code == 200

    response = await client.post(
        f"/v1/myevents/by_id/skater/{programme['id']}/enrollments/request",
        headers=auth(member),
    )

    assert response.status_code == 204, response.text
    assert (
        await enrollment_of(client, super_admin, programme["id"], "skater")
        == "requested"
    )


@pytest.mark.requirement("enrollment:R15")
@pytest.mark.asyncio
async def test_should_allow_withdrawal_when_event_has_ended(
    client: AsyncClient, db_session: AsyncSession
):
    """Exit-side flows stay open to a super-admin cleaning up an ended event."""
    super_admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=-10)
    programme = await create_programme(client, super_admin, venue, start=start)
    assert (
        await assign(client, super_admin, programme["id"], "skater")
    ).status_code == 204
    await set_cutoff(db_session, programme["id"], start + 3 * DAY_MS)

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/remove",
        json={"membernames": ["skater"]},
        headers=auth(super_admin),
    )

    assert response.status_code == 204, response.text
    assert (
        await enrollment_of(client, super_admin, programme["id"], "skater") == "removed"
    )


@pytest.mark.requirement("enrollment:R16")
@pytest.mark.asyncio
async def test_should_allow_invite_when_programme_has_cutoff_but_still_runs(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    programme = await create_programme(client, super_admin, venue, start=start)
    assert (
        await terminate(client, super_admin, programme["id"], start + 5 * DAY_MS)
    ).status_code == 200

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/invite",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, programme["id"], "skater") == "invited"


# ---------------------------------------------------------------------------
# R6–R9: extend and extend-indefinitely
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R6")
@pytest.mark.asyncio
async def test_should_move_cutoff_later_when_terminated_programme_is_extended(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (
        await terminate(client, admin, programme["id"], start + 3 * DAY_MS)
    ).status_code == 200

    response = await extend(client, admin, programme["id"], start + 6 * DAY_MS)

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] == start + 6 * DAY_MS
    statuses = _statuses(
        await list_occurrences(
            client, admin, start - HOUR_MS, start + 7 * DAY_MS, event_id=programme["id"]
        )
    )
    assert statuses[start + 4 * DAY_MS] == "scheduled"
    assert statuses[start + 6 * DAY_MS] == "cancelled"


@pytest.mark.requirement("programme:R6")
@pytest.mark.asyncio
async def test_should_reject_extend_when_new_cutoff_is_not_an_occurrence_start(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (
        await terminate(client, admin, programme["id"], start + 3 * DAY_MS)
    ).status_code == 200

    response = await extend(
        client, admin, programme["id"], start + 6 * DAY_MS + HOUR_MS
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY"
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] == start + 3 * DAY_MS


@pytest.mark.requirement("programme:R6")
@pytest.mark.asyncio
async def test_should_reject_extend_when_programme_is_open_ended(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await extend(client, admin, programme["id"], start + 6 * DAY_MS)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("programme:R6a")
@pytest.mark.asyncio
async def test_should_reject_extend_when_current_cutoff_has_passed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-10)
    programme = await create_programme(client, admin, venue, start=start)
    await set_cutoff(db_session, programme["id"], start + 3 * DAY_MS)

    response = await extend(client, admin, programme["id"], start + 20 * DAY_MS)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] == start + 3 * DAY_MS


@pytest.mark.requirement("notifications:R78")
@pytest.mark.requirement("programme:R7")
@pytest.mark.asyncio
async def test_should_cancel_occurrences_and_notify_when_cutoff_moved_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    assert (
        await terminate(client, admin, programme["id"], start + 6 * DAY_MS)
    ).status_code == 200

    response = await extend(client, admin, programme["id"], start + 3 * DAY_MS)

    assert response.status_code == 200, response.text
    statuses = _statuses(
        await list_occurrences(
            client, admin, start - HOUR_MS, start + 7 * DAY_MS, event_id=programme["id"]
        )
    )
    assert statuses[start + 2 * DAY_MS] == "scheduled"
    assert statuses[start + 3 * DAY_MS] == "cancelled"
    assert statuses[start + 5 * DAY_MS] == "cancelled"
    notices = await notifications_for(db_session, "skater", "event.extended")
    assert len(notices) == 1
    assert notices[0].payload["data"]["cutoffTimeUtc"] == start + 3 * DAY_MS
    assert notices[0].payload["data"]["previousCutoffUtc"] == start + 6 * DAY_MS


@pytest.mark.requirement("programme:R8")
@pytest.mark.asyncio
async def test_should_clear_cutoff_when_programme_extended_indefinitely(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (
        await terminate(client, admin, programme["id"], start + 3 * DAY_MS)
    ).status_code == 200

    response = await extend_indefinitely(client, admin, programme["id"])

    assert response.status_code == 200, response.text
    assert response.json()["untilTimeUtc"] is None
    assert "UNTIL" not in (response.json()["rrule"] or "")
    statuses = _statuses(
        await list_occurrences(
            client, admin, start - HOUR_MS, start + 7 * DAY_MS, event_id=programme["id"]
        )
    )
    assert statuses[start + 5 * DAY_MS] == "scheduled"


@pytest.mark.requirement("programme:R8")
@pytest.mark.asyncio
async def test_should_reject_extend_indefinitely_when_cutoff_has_passed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-10)
    programme = await create_programme(client, admin, venue, start=start)
    await set_cutoff(db_session, programme["id"], start + 3 * DAY_MS)

    response = await extend_indefinitely(client, admin, programme["id"])

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("programme:R9")
@pytest.mark.asyncio
async def test_should_terminate_current_schedule_when_programme_was_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other_venue = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    split_cutoff = start + 3 * DAY_MS
    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=split_cutoff,
        venueId=other_venue,
    )
    assert done.status_code == 200, done.text

    response = await terminate(client, admin, programme["id"], start + 6 * DAY_MS)

    assert response.status_code == 200, response.text
    schedules = await client.get(
        f"/v1/events/by_id/{programme['id']}/schedules", headers=auth(admin)
    )
    assert schedules.status_code == 200, schedules.text
    first, second = schedules.json()
    assert first["effectiveUntilUtc"] == split_cutoff
    assert second["effectiveFromUtc"] == split_cutoff
    assert second["effectiveUntilUtc"] == start + 6 * DAY_MS


# ---------------------------------------------------------------------------
# R10–R10c, L20, L20a, credit R73a–R73d: termination releases bound credit
# ---------------------------------------------------------------------------


async def _terminated_programme_with_bound_credit(
    client: AsyncClient, db_session: AsyncSession, *, start: int, cutoff: int
) -> tuple[str, int, str]:
    """A programme with one member holding ten bound credits, terminated at ``cutoff``."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, start=start)
    account = await open_account(
        client, admin, "skater", credits=10, event_id=programme["id"]
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    done = await terminate(client, admin, programme["id"], cutoff)
    assert done.status_code == 200, done.text
    return admin, programme["id"], str(account["accountId"])


async def _accounts_of(client: AsyncClient, token: str, member: str) -> list[dict]:
    response = await client.get(
        "/v1/credits/accounts", params={"membername": member}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.requirement("notifications:R110")
@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R10")
@pytest.mark.requirement("credit:R73a")
@pytest.mark.asyncio
async def test_should_release_bound_balance_when_cutoff_passes(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    cutoff = start + 3 * DAY_MS
    admin, programme_id, bound = await _terminated_programme_with_bound_credit(
        client, db_session, start=start, cutoff=cutoff
    )

    await sweep_credit_settlements(db_session, cutoff + MINUTE_MS)
    await db_session.commit()

    assert await balance_of(client, admin, bound) == 0
    general = [
        a for a in await _accounts_of(client, admin, "skater") if a["kind"] == "general"
    ]
    assert len(general) == 1
    assert general[0]["balance"] == 10
    notices = await notifications_for(db_session, "skater", "credit.released")
    assert len(notices) == 1
    assert notices[0].payload["data"]["eventId"] == programme_id


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R10a")
@pytest.mark.asyncio
async def test_should_reject_terminate_when_a_credit_disposition_is_supplied(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/terminate",
        json={
            "reason": "Season over",
            "cutoffTimeUtc": start + 3 * DAY_MS,
            "version": 1,
            "creditDisposition": {
                "penalty": 0,
                "validFromUtc": at(),
                "validUntilUtc": at(days=90),
                "reason": "x",
            },
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["untilTimeUtc"] is None


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R10b")
@pytest.mark.asyncio
async def test_should_let_released_credit_fund_another_programme(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    cutoff = start + 3 * DAY_MS
    admin, _, _ = await _terminated_programme_with_bound_credit(
        client, db_session, start=start, cutoff=cutoff
    )
    await sweep_credit_settlements(db_session, cutoff + MINUTE_MS)
    await db_session.commit()
    venue = await create_venue(client, admin, "Rink B")
    other = await create_programme(client, admin, venue, start=at(days=10))

    response = await assign(client, admin, other["id"], "skater")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, other["id"], "skater") == "assigned"


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("programme:R10c")
@pytest.mark.asyncio
async def test_should_release_nothing_when_cutoff_moved_earlier(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    admin, programme_id, bound = await _terminated_programme_with_bound_credit(
        client, db_session, start=start, cutoff=start + 6 * DAY_MS
    )

    response = await extend(client, admin, programme_id, start + 3 * DAY_MS)
    await sweep_credit_settlements(db_session, at())
    await db_session.commit()

    assert response.status_code == 200, response.text
    assert await balance_of(client, admin, bound) == 10
    assert [a["kind"] for a in await _accounts_of(client, admin, "skater")] == ["event"]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("lifecycle:L20")
@pytest.mark.requirement("credit:R73b")
@pytest.mark.asyncio
async def test_should_keep_balance_bound_when_cutoff_has_not_passed(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    cutoff = start + 3 * DAY_MS
    admin, _, bound = await _terminated_programme_with_bound_credit(
        client, db_session, start=start, cutoff=cutoff
    )

    await sweep_credit_settlements(db_session, cutoff - MINUTE_MS)
    await db_session.commit()

    assert await balance_of(client, admin, bound) == 10
    assert [a["kind"] for a in await _accounts_of(client, admin, "skater")] == ["event"]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R73c")
@pytest.mark.asyncio
async def test_should_keep_validity_window_when_balance_is_released(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    cutoff = start + 3 * DAY_MS
    admin, _, bound = await _terminated_programme_with_bound_credit(
        client, db_session, start=start, cutoff=cutoff
    )
    before = {a["accountId"]: a for a in await _accounts_of(client, admin, "skater")}[
        bound
    ]

    await sweep_credit_settlements(db_session, cutoff + MINUTE_MS)
    await db_session.commit()

    general = [
        a for a in await _accounts_of(client, admin, "skater") if a["kind"] == "general"
    ]
    assert len(general) == 1
    assert general[0]["validFromUtc"] == before["validFromUtc"]
    assert general[0]["validUntilUtc"] == before["validUntilUtc"]
    assert general[0]["balance"] == 10


@pytest.mark.requirement("notifications:R110")
@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("lifecycle:L20a")
@pytest.mark.requirement("credit:R73d")
@pytest.mark.asyncio
async def test_should_release_once_when_sweep_runs_repeatedly(
    client: AsyncClient, db_session: AsyncSession
):
    start = at(days=2)
    cutoff = start + 3 * DAY_MS
    admin, _, _ = await _terminated_programme_with_bound_credit(
        client, db_session, start=start, cutoff=cutoff
    )

    await sweep_credit_settlements(db_session, cutoff + MINUTE_MS)
    await db_session.commit()
    await sweep_credit_settlements(db_session, cutoff + HOUR_MS)
    await db_session.commit()

    accounts = await _accounts_of(client, admin, "skater")
    assert sorted(a["kind"] for a in accounts) == ["event", "general"]
    assert sum(a["balance"] for a in accounts) == 10
    assert len(await notifications_for(db_session, "skater", "credit.released")) == 1


# ---------------------------------------------------------------------------
# R33, L4: audit
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R33")
@pytest.mark.requirement("lifecycle:L4")
@pytest.mark.asyncio
async def test_should_audit_cutoff_before_and_after_when_programme_terminated_and_extended(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    assert (
        await terminate(
            client, admin, programme["id"], start + 3 * DAY_MS, "Season over"
        )
    ).status_code == 200
    assert (
        await extend(client, admin, programme["id"], start + 6 * DAY_MS)
    ).status_code == 200
    assert (
        await extend_indefinitely(client, admin, programme["id"])
    ).status_code == 200

    terminated = await audit_rows(db_session, "terminate_event")
    assert len(terminated) == 1
    assert terminated[0].actor_username == "admin"
    assert terminated[0].resource_id == str(programme["id"])
    assert '"reason": "Season over"' in terminated[0].details
    assert '"cutoff_before": null' in terminated[0].details
    assert f'"cutoff_after": {start + 3 * DAY_MS}' in terminated[0].details
    extended = await audit_rows(db_session, "extend_event")
    assert [row.actor_username for row in extended] == ["admin", "admin"]
    assert f'"cutoff_before": {start + 3 * DAY_MS}' in extended[0].details
    assert f'"cutoff_after": {start + 6 * DAY_MS}' in extended[0].details
    assert f'"cutoff_before": {start + 6 * DAY_MS}' in extended[1].details
    assert '"cutoff_after": null' in extended[1].details
