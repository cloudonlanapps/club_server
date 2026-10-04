"""Conflict detection as one gate module (#384 phase 0).

Programme R30–R31c, one-off R20–R21b, camp R31/R39/R56, enrollment
R29a/R29b/R30: behaviour follows the pairing, programmes are compared as
rules, camps and one-offs are bounded by the scheduling horizon, and every
gate uses one definition of overlap.
"""

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    WEEK_MS,
    assign,
    at,
    auth,
    create_camp,
    create_event,
    create_oneoff,
    create_programme,
    create_venue,
    drop,
    enrollment_of,
    floor_s,
    get_event,
    notifications_for,
    reinstate,
    reschedule_occurrence,
    split,
    terminate,
    version_of,
)


def monday_at(hour: int, *, weeks_ahead: int = 1) -> int:
    """A Monday at ``hour``:00 UTC, at least ``weeks_ahead`` full weeks out.

    Skips the coming Monday: a test that places something a few days
    *before* the returned Monday must still land in the future, whatever
    the weekday the suite runs on (#416).
    """
    today = datetime.now(timezone.utc).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    days_until_monday = (7 - today.weekday()) % 7 or 7
    monday = today + timedelta(days=days_until_monday + 7 * weeks_ahead)
    return floor_s(int(monday.timestamp() * 1000))


async def _conflict_notices(db_session: AsyncSession) -> list:
    return await notifications_for(db_session, "admin", "event.conflict_detected")


# ---------------------------------------------------------------------------
# R30, R30a, R30a1: programme × programme, compared as rules
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R30")
@pytest.mark.asyncio
async def test_should_block_programme_when_another_programme_holds_the_venue_slot(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    first = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    response = await client.post(
        "/v1/events",
        json={
            "title": "Clash",
            "type": "programme",
            "venueId": venue,
            "startTimeUtc": monday + 30 * 60 * 1000,
            "endTimeUtc": monday + 90 * 60 * 1000,
            "rrule": "FREQ=WEEKLY;BYDAY=MO,WE",
        },
        headers=auth(admin),
    )

    assert response.status_code == 409, response.text
    assert [c["eventId"] for c in response.json()["detail"]["venueConflicts"]] == [
        first["id"]
    ]
    listed = await client.get("/v1/events", headers=auth(admin))
    assert [e["id"] for e in listed.json()["items"]] == [first["id"]]


@pytest.mark.requirement("programme:R30a")
@pytest.mark.asyncio
async def test_should_allow_programme_when_weekday_sets_do_not_intersect(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    _ = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    response = await create_event(
        client,
        admin,
        event_type="programme",
        venue_id=venue,
        start=monday + DAY_MS,
        end=monday + DAY_MS + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=TU",
    )

    assert response["rrule"] == "FREQ=WEEKLY;BYDAY=TU"


@pytest.mark.requirement("programme:R30a")
@pytest.mark.asyncio
async def test_should_allow_programme_when_date_ranges_do_not_overlap(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    first = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    ended = await terminate(client, admin, first["id"], monday + 2 * WEEK_MS)
    assert ended.status_code == 200, ended.text

    response = await create_event(
        client,
        admin,
        event_type="programme",
        venue_id=venue,
        start=monday + 2 * WEEK_MS,
        end=monday + 2 * WEEK_MS + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    assert response["startTimeUtc"] == monday + 2 * WEEK_MS


@pytest.mark.requirement("programme:R30a")
@pytest.mark.asyncio
async def test_should_allow_programme_when_overlapping_range_holds_no_shared_weekday(
    client: AsyncClient, db_session: AsyncSession
):
    """Two Monday programmes whose ranges overlap only Thursday to Sunday."""
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    first = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    ended = await terminate(client, admin, first["id"], monday + WEEK_MS)
    assert ended.status_code == 200, ended.text

    response = await create_event(
        client,
        admin,
        event_type="programme",
        venue_id=venue,
        start=monday + 3 * DAY_MS,
        end=monday + 3 * DAY_MS + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    assert response["rrule"] == "FREQ=WEEKLY;BYDAY=MO"


@pytest.mark.requirement("programme:R30a")
@pytest.mark.asyncio
async def test_should_allow_programme_when_time_windows_touch_but_do_not_overlap(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    _ = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    response = await create_event(
        client,
        admin,
        event_type="programme",
        venue_id=venue,
        start=monday + HOUR_MS,
        end=monday + 2 * HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    assert response["startTimeUtc"] == monday + HOUR_MS


@pytest.mark.requirement("programme:R30a")
@pytest.mark.asyncio
async def test_should_allow_programme_when_venue_differs(
    client: AsyncClient, db_session: AsyncSession
):
    """Same slot, other rink, other organizer: neither gate has a finding."""
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    monday = monday_at(18)
    _ = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    response = await create_event(
        client,
        coach,
        event_type="programme",
        venue_id=other,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    assert response["venueId"] == other
    assert response["organizerName"] == "other_organizer"


@pytest.mark.requirement("programme:R30a1")
@pytest.mark.asyncio
async def test_should_block_programme_when_window_crossing_midnight_meets_next_day(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(23)
    _ = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + 90 * 60 * 1000,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    response = await client.post(
        "/v1/events",
        json={
            "title": "Tuesday small hours",
            "type": "programme",
            "venueId": venue,
            "startTimeUtc": monday + HOUR_MS,
            "endTimeUtc": monday + 2 * HOUR_MS,
            "rrule": "FREQ=WEEKLY;BYDAY=TU",
        },
        headers=auth(admin),
    )

    assert response.status_code == 409, response.text


@pytest.mark.requirement("programme:R30b")
@pytest.mark.asyncio
async def test_should_reject_interval_so_rule_comparison_stays_exact(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)

    response = await client.post(
        "/v1/events",
        json={
            "title": "Fortnightly",
            "type": "programme",
            "venueId": venue,
            "startTimeUtc": monday,
            "endTimeUtc": monday + HOUR_MS,
            "rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO",
        },
        headers=auth(admin),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_PROGRAMME"


# ---------------------------------------------------------------------------
# R30c, R30d, R31, R31a: what is reported and what is blocked
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R30c")
@pytest.mark.asyncio
async def test_should_report_not_block_when_programme_and_camp_share_a_slot(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    camp = await create_camp(
        client, admin, venue, start=monday, end=monday + HOUR_MS, count=3
    )
    other_venue = await create_venue(client, admin, "Rink B")
    _ = await create_camp(client, admin, other_venue, start=monday, count=3)
    second_programme = await create_programme(
        client,
        admin,
        other_venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
        organizerName="other_organizer",
    )

    notices = await _conflict_notices(db_session)
    reported = {n.payload["data"]["eventId"] for n in notices}
    assert camp["id"] in reported
    assert second_programme["id"] in reported
    assert programme["id"] not in reported


@pytest.mark.requirement("programme:R30d")
@pytest.mark.asyncio
async def test_should_not_conflict_with_itself_when_programme_is_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=monday + 2 * WEEK_MS,
        coachNames=["bob"],
    )

    assert response.status_code == 200, response.text
    assert await _conflict_notices(db_session) == []


@pytest.mark.requirement("programme:R31")
@pytest.mark.asyncio
async def test_should_block_split_when_new_schedule_clashes_with_another_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    _ = await create_programme(
        client,
        admin,
        other,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
        organizerName="other_organizer",
    )

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=monday + 2 * WEEK_MS,
        venueId=other,
    )

    assert response.status_code == 409, response.text
    assert (await get_event(client, admin, programme["id"]))["venueId"] == venue


@pytest.mark.requirement("programme:R31a")
@pytest.mark.asyncio
async def test_should_report_not_block_when_single_occurrence_is_moved_onto_a_clash(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    occupied = await create_programme(
        client,
        admin,
        other,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
        organizerName="other_organizer",
    )

    response = await reschedule_occurrence(
        client, admin, programme["id"], monday + WEEK_MS, newVenueId=other
    )

    assert response.status_code == 204, response.text
    notices = await _conflict_notices(db_session)
    assert len(notices) == 1
    assert notices[0].payload["data"]["eventId"] == programme["id"]
    assert notices[0].payload["data"]["occurrenceTimeUtc"] == monday + WEEK_MS
    assert [c["eventId"] for c in notices[0].payload["data"]["conflictingEvents"]] == [
        occupied["id"]
    ]


@pytest.mark.requirement("programme:R31b")
def test_should_expose_four_gates_in_one_module_and_no_other_implementation():
    gates = importlib.import_module("club_server.services.conflict_gates")
    event_service = importlib.import_module("club_server.services.event")
    enrollment_service = importlib.import_module("club_server.services.enrollment")

    assert {g.value for g in gates.ConflictGate} == {
        "venue",
        "organizer",
        "coach",
        "member",
    }
    assert callable(gates.overlaps)
    assert not hasattr(event_service.EventService, "check_conflict")
    assert not hasattr(
        enrollment_service.EnrollmentService, "check_enrollment_time_conflict"
    )
    with pytest.raises((ImportError, AttributeError)):
        conflict = importlib.import_module("club_server.services.conflict")
        _ = conflict.ConflictService


@pytest.mark.requirement("programme:R31c")
@pytest.mark.asyncio
async def test_should_return_advisory_findings_and_raise_only_blocking_ones(
    client: AsyncClient, db_session: AsyncSession
):
    from club_server.exceptions import EventConflictException
    from club_server.services.conflict_gates import (
        ConflictGate,
        GatePolicy,
        ScheduleTarget,
        check_conflicts,
    )

    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    target = ScheduleTarget(
        event_type="programme",
        venue_id=venue,
        start_time=monday,
        end_time=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
        effective_from=monday,
    )

    advisory = await check_conflicts(
        db_session, target, {ConflictGate.venue: GatePolicy.advise}
    )
    assert [f.event_id for f in advisory.findings] == [programme["id"]]
    assert advisory.findings[0].gate is ConflictGate.venue

    with pytest.raises(EventConflictException):
        await check_conflicts(
            db_session, target, {ConflictGate.venue: GatePolicy.block}
        )

    silent = await check_conflicts(
        db_session, target, {ConflictGate.organizer: GatePolicy.block}
    )
    assert silent.findings == []


# ---------------------------------------------------------------------------
# One-off R20–R21b, camp R31/R39/R56, enrollment R29a/R29b/R30
# ---------------------------------------------------------------------------


@pytest.mark.requirement("oneoff:R20")
@pytest.mark.asyncio
async def test_should_report_not_block_when_oneoff_clashes_with_anything(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204

    oneoff = await create_oneoff(
        client, admin, venue, start=monday, end=monday + HOUR_MS, visibility="public"
    )
    joined = await client.post(
        f"/v1/myevents/by_id/skater/{oneoff['id']}/enrollments/request",
        headers=auth(member),
    )

    assert joined.status_code == 204, joined.text
    assert await enrollment_of(client, admin, oneoff["id"], "skater") == "requested"
    assert {
        n.payload["data"]["eventId"] for n in await _conflict_notices(db_session)
    } == {oneoff["id"]}


@pytest.mark.requirement("oneoff:R20a")
@pytest.mark.asyncio
async def test_should_reject_oneoff_and_camp_when_start_is_beyond_the_horizon(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    far = at(days=7 * 53)

    oneoff = await create_event(
        client,
        admin,
        event_type="oneOff",
        venue_id=venue,
        start=far,
        expected_status=422,
    )
    camp = await create_event(
        client,
        admin,
        event_type="camp",
        venue_id=venue,
        start=far,
        rrule="FREQ=DAILY;COUNT=3",
        expected_status=422,
    )
    programme = await create_programme(client, admin, venue, start=far)

    assert oneoff["detail"]["code"] == "BEYOND_SCHEDULING_HORIZON"
    assert camp["detail"]["code"] == "BEYOND_SCHEDULING_HORIZON"
    assert programme["startTimeUtc"] == far


@pytest.mark.requirement("oneoff:R20a")
@pytest.mark.asyncio
async def test_should_accept_oneoff_when_start_is_inside_the_horizon(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    near = at(days=7 * 51)

    oneoff = await create_oneoff(client, admin, venue, start=near)

    assert (await get_event(client, admin, oneoff["id"]))["startTimeUtc"] == near


@pytest.mark.requirement("oneoff:R20b")
@pytest.mark.asyncio
async def test_should_apply_configured_horizon_when_deployment_sets_one(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    two_weeks = at(days=14)
    assert settings.scheduling_horizon_weeks == 52

    monkeypatch.setattr(settings, "scheduling_horizon_weeks", 1)
    refused = await create_event(
        client,
        admin,
        event_type="oneOff",
        venue_id=venue,
        start=two_weeks,
        expected_status=422,
    )
    monkeypatch.setattr(settings, "scheduling_horizon_weeks", 52)
    accepted = await create_oneoff(client, admin, venue, start=two_weeks)

    assert refused["detail"]["code"] == "BEYOND_SCHEDULING_HORIZON"
    assert accepted["startTimeUtc"] == two_weeks


@pytest.mark.requirement("oneoff:R21")
@pytest.mark.asyncio
async def test_should_report_clash_when_oneoff_is_rescheduled_or_reinstated_onto_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    quiet = await create_venue(client, admin, "Quiet")
    monday = monday_at(18)
    _ = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    oneoff = await create_oneoff(client, admin, quiet, start=monday - 3 * DAY_MS)
    assert await _conflict_notices(db_session) == []

    moved = await client.post(
        f"/v1/events/by_id/{oneoff['id']}/reschedule",
        json={
            "version": await version_of(client, admin, oneoff["id"]),
            "startTimeUtc": monday,
            "venueId": venue,
        },
        headers=auth(admin),
    )
    assert moved.status_code == 200, moved.text
    assert len(await _conflict_notices(db_session)) == 1

    assert (await drop(client, admin, oneoff["id"])).status_code == 200
    back = await reinstate(client, admin, oneoff["id"])
    assert back.status_code == 200, back.text
    assert len(await _conflict_notices(db_session)) == 2


@pytest.mark.requirement("oneoff:R21a")
@pytest.mark.asyncio
async def test_should_use_shared_gates_when_oneoff_is_checked(
    client: AsyncClient, db_session: AsyncSession
):
    from club_server.services.conflict_gates import (
        ConflictGate,
        GatePolicy,
        ScheduleTarget,
        check_conflicts,
    )

    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    camp = await create_camp(client, admin, venue, start=monday, count=3)
    target = ScheduleTarget(
        event_type="oneOff",
        venue_id=venue,
        start_time=monday + DAY_MS,
        end_time=monday + DAY_MS + HOUR_MS,
        rrule=None,
        effective_from=monday + DAY_MS,
        effective_until=monday + DAY_MS + HOUR_MS,
    )

    report = await check_conflicts(
        db_session, target, {ConflictGate.venue: GatePolicy.advise}
    )

    assert [f.event_id for f in report.findings] == [camp["id"]]
    assert report.findings[0].occurrences == [
        (
            monday + DAY_MS,
            monday + DAY_MS + HOUR_MS,
            monday + DAY_MS,
            monday + DAY_MS + HOUR_MS,
        )
    ]


@pytest.mark.requirement("oneoff:R21b")
@pytest.mark.asyncio
async def test_should_apply_reschedule_guards_when_oneoff_schedule_is_updated(
    client: AsyncClient, db_session: AsyncSession
):
    super_admin = await create_admin_user(db_session)
    admin = await create_regular_admin_user(db_session)
    venue = await create_venue(client, super_admin)
    start = at(days=2)
    oneoff = await create_oneoff(client, super_admin, venue, start=start)

    earlier = await client.post(
        f"/v1/events/by_id/{oneoff['id']}/reschedule",
        json={
            "version": await version_of(client, admin, oneoff["id"]),
            "startTimeUtc": start - HOUR_MS,
        },
        headers=auth(admin),
    )
    via_patch = await client.patch(
        f"/v1/events/by_id/{oneoff['id']}",
        json={"startTimeUtc": start - HOUR_MS, "version": 1},
        headers=auth(admin),
    )

    assert earlier.status_code == 422, earlier.text
    assert earlier.json()["detail"]["code"] == "POSTPONE_ONLY"
    assert via_patch.status_code == 422, via_patch.text
    assert (await get_event(client, admin, oneoff["id"]))["startTimeUtc"] == start


@pytest.mark.requirement("camps:R31")
@pytest.mark.asyncio
async def test_should_accept_any_type_when_conflict_report_is_requested(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204

    report = await client.post(
        "/v1/events/check-conflict",
        json={
            "type": "programme",
            "venueId": venue,
            "startTimeUtc": monday,
            "endTimeUtc": monday + HOUR_MS,
            "rrule": "FREQ=WEEKLY;BYDAY=MO",
        },
        headers=auth(admin),
    )
    users = await client.post(
        f"/v1/events/by_id/{programme['id']}/check-user-conflicts",
        json={"usernames": ["skater"]},
        headers=auth(admin),
    )

    assert report.status_code == 200, report.text
    assert [c["eventId"] for c in report.json()["venueConflicts"]] == [programme["id"]]
    assert users.status_code == 200, users.text
    assert users.json()["userConflicts"] == []


@pytest.mark.requirement("camps:R39")
@pytest.mark.requirement("enrollment:R30")
@pytest.mark.asyncio
async def test_should_block_join_only_when_clash_is_programme_against_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    camp = await create_camp(client, admin, venue, start=monday, count=3)
    assert (await assign(client, admin, camp["id"], "skater")).status_code == 204
    other = await create_venue(client, admin, "Rink B")
    programme = await create_programme(
        client,
        admin,
        other,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    against_camp = await assign(client, admin, programme["id"], "skater")
    assert against_camp.status_code == 204, against_camp.text

    third = await create_venue(client, admin, "Rink C")
    clashing = await create_programme(
        client,
        admin,
        third,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
        organizerName="other_organizer",
    )
    against_programme = await assign(client, admin, clashing["id"], "skater")

    assert against_programme.status_code == 409, against_programme.text
    assert against_programme.json()["detail"]["code"] == "TIME_CONFLICT"
    assert await enrollment_of(client, admin, clashing["id"], "skater") is None


@pytest.mark.requirement("camps:R56")
@pytest.mark.asyncio
async def test_should_assign_to_camp_when_member_has_an_overlapping_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    monday = monday_at(18)
    programme = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    other = await create_venue(client, admin, "Rink B")
    camp = await create_camp(client, admin, other, start=monday, count=3)

    response = await assign(client, admin, camp["id"], "skater")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, camp["id"], "skater") == "assigned"


@pytest.mark.requirement("enrollment:R29a")
@pytest.mark.asyncio
async def test_should_allow_join_when_programmes_overlap_in_window_but_not_weekday(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    monday = monday_at(18)
    on_monday = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert (await assign(client, admin, on_monday["id"], "skater")).status_code == 204
    on_tuesday = await create_programme(
        client,
        admin,
        other,
        start=monday + DAY_MS,
        end=monday + DAY_MS + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=TU",
    )

    response = await assign(client, admin, on_tuesday["id"], "skater")

    assert response.status_code == 204, response.text
    assert await enrollment_of(client, admin, on_tuesday["id"], "skater") == "assigned"


@pytest.mark.requirement("enrollment:R29b")
@pytest.mark.asyncio
async def test_should_count_terminated_programme_when_it_still_runs_to_its_cutoff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    monday = monday_at(18)
    ending = await create_programme(
        client,
        admin,
        venue,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert (await assign(client, admin, ending["id"], "skater")).status_code == 204
    assert (
        await terminate(client, admin, ending["id"], monday + 4 * WEEK_MS)
    ).status_code == 200
    overlapping = await create_programme(
        client,
        admin,
        other,
        start=monday,
        end=monday + HOUR_MS,
        rrule="FREQ=WEEKLY;BYDAY=MO",
        organizerName="other_organizer",
    )

    response = await assign(client, admin, overlapping["id"], "skater")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TIME_CONFLICT"
