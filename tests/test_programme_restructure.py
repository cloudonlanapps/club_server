"""Corrections, splits and the schedule model (#384 phase 0).

Programme R21–R26 and R34, plus the acceptance criteria of #388: an event is
one id for life, its timetable is a contiguous sequence of schedules, and the
chain columns are gone from the public surface.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .eligibility_helpers import with_event_band
from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    assign,
    at,
    audit_rows,
    auth,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    enrollment_of,
    get_event,
    list_occurrences,
    list_user_occurrences,
    occurrence_version,
    split,
    version_of,
)


async def _correct(client: AsyncClient, token: str, event_id: int, **body: object):
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}/correction", json=body, headers=auth(token)
    )


async def _schedules(client: AsyncClient, token: str, event_id: int) -> list[dict]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/schedules", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# R21–R22a: correction
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R21")
@pytest.mark.asyncio
async def test_should_apply_correction_to_every_occurrence_when_title_is_fixed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=-3)
    programme = await create_programme(client, admin, venue, start=start)

    response = await _correct(
        client,
        admin,
        programme["id"],
        title="Junior Squad",
        description="Fixed",
        visibility="public",
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert (fetched["title"], fetched["description"], fetched["visibility"]) == (
        "Junior Squad",
        "Fixed",
        "public",
    )
    listed = await list_occurrences(
        client, admin, start - HOUR_MS, start + 5 * DAY_MS, event_id=programme["id"]
    )
    assert {o["eventTitle"] for o in listed} == {"Junior Squad"}


@pytest.mark.requirement("programme:R21a")
@pytest.mark.asyncio
async def test_should_reject_correction_when_it_names_coaches(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, coachNames=["ann"])

    response = await _correct(client, admin, programme["id"], coachNames=["bob"])

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["coachNames"] == ["ann"]


@pytest.mark.requirement("programme:R22")
@pytest.mark.asyncio
async def test_should_reject_correction_when_it_carries_scheduling_fields(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await _correct(
        client, admin, programme["id"], startTimeUtc=start + HOUR_MS
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["startTimeUtc"] == start


@pytest.mark.requirement("programme:R22a")
@pytest.mark.asyncio
async def test_should_correct_featured_flag_and_gallery_when_supplied(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)

    response = await _correct(
        client, admin, programme["id"], isFeatured=True, galleryUris=["a.jpg"]
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["isFeatured"] is True
    assert fetched["galleryUris"] == ["a.jpg"]


# ---------------------------------------------------------------------------
# R23–R26: split
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R23")
@pytest.mark.asyncio
async def test_should_keep_event_id_and_enrollments_when_programme_is_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    cutoff = start + 3 * DAY_MS

    response = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=other
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == programme["id"]
    assert response.json()["venueId"] == other
    assert response.json()["untilTimeUtc"] is None
    assert await enrollment_of(client, admin, programme["id"], "skater") == "assigned"
    mine = await list_user_occurrences(
        client,
        member,
        "skater",
        start - HOUR_MS,
        start + 5 * DAY_MS,
        event_id=programme["id"],
    )
    assert [o["venueId"] for o in mine] == [venue, venue, venue, other, other]


@pytest.mark.requirement("programme:R24")
@pytest.mark.asyncio
async def test_should_reject_split_when_cutoff_is_not_an_occurrence_start(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 3 * DAY_MS + HOUR_MS,
        venueId=other,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY"
    assert (await get_event(client, admin, programme["id"]))["venueId"] == venue


@pytest.mark.requirement("programme:R24")
@pytest.mark.asyncio
async def test_should_reject_split_when_cutoff_is_back_dated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 2 * DAY_MS,
        venueId=other,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CUTOFF_TOO_SOON"
    assert (await get_event(client, admin, programme["id"]))["venueId"] == venue


@pytest.mark.requirement("programme:R24")
@pytest.mark.asyncio
async def test_should_reject_split_when_cutoff_is_omitted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    programme = await create_programme(client, admin, venue)

    response = await split(client, admin, programme["id"], venueId=other)

    assert response.status_code == 422, response.text
    assert (await get_event(client, admin, programme["id"]))["venueId"] == venue


@pytest.mark.requirement("programme:R24a")
@pytest.mark.asyncio
async def test_should_open_new_schedule_at_cutoff_when_programme_is_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS
    done = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=other
    )
    assert done.status_code == 200, done.text

    first, second = await _schedules(client, admin, programme["id"])

    assert first["effectiveFromUtc"] == start
    assert first["effectiveUntilUtc"] == cutoff
    assert second["effectiveFromUtc"] == cutoff
    assert second["effectiveUntilUtc"] is None
    assert (first["venueId"], second["venueId"]) == (venue, other)
    listed = {
        o["occurrenceTimeUtc"]: o
        for o in await list_occurrences(
            client, admin, start - HOUR_MS, start + 5 * DAY_MS, event_id=programme["id"]
        )
    }
    assert listed[cutoff]["venueId"] == other
    assert listed[cutoff - DAY_MS]["venueId"] == venue


@pytest.mark.requirement("programme:R25")
@pytest.mark.asyncio
async def test_should_reject_generic_update_when_event_is_a_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)

    response = await client.patch(
        f"/v1/events/by_id/{programme['id']}",
        json={"title": "Nope", "version": 1},
        headers=auth(admin),
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_EVENT_TYPE"
    assert (await get_event(client, admin, programme["id"]))["title"] == programme[
        "title"
    ]


@pytest.mark.requirement("programme:R25a")
@pytest.mark.asyncio
async def test_should_reach_eligibility_fields_through_correction(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)
    dob_after = 946684800000  # 2000-01-01T00:00:00Z
    dob_before = 1262304000000  # 2010-01-01T00:00:00Z

    response = await _correct(
        client,
        admin,
        programme["id"],
        gender="female",
        **with_event_band(
            {"dobOnOrAfterUtc": dob_after, "dobOnOrBeforeUtc": dob_before},
            programme["startTimeUtc"],
        ),
    )

    assert response.status_code == 200, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["gender"] == "female"
    assert fetched["dobOnOrAfterUtc"] == dob_after
    assert fetched["dobOnOrBeforeUtc"] == dob_before


@pytest.mark.requirement("programme:R25a")
@pytest.mark.asyncio
async def test_should_reach_timetable_and_staffing_through_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(
        client, admin, venue, start=start, coachNames=["ann"]
    )
    cutoff = start + 3 * DAY_MS

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=cutoff,
        coachNames=["bob"],
        organizerName="admin",
        sessions=[{"name": "Skate", "periodMinutes": 60}],
    )

    assert response.status_code == 200, response.text
    first, second = await _schedules(client, admin, programme["id"])
    assert first["coachNames"] == ["ann"]
    assert second["coachNames"] == ["bob"]
    assert second["organizerName"] == "admin"
    assert second["sessions"] == [{"name": "Skate", "periodMinutes": 60}]
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["coachNames"] == ["bob"]
    assert fetched["sessions"] == [{"name": "Skate", "periodMinutes": 60}]


@pytest.mark.requirement("programme:R25a")
@pytest.mark.asyncio
async def test_should_reject_split_when_it_renames_the_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 3 * DAY_MS,
        venueId=other,
        title="Season 2",
    )

    assert response.status_code == 422, response.text
    fetched = await get_event(client, admin, programme["id"])
    assert fetched["title"] == programme["title"]
    assert fetched["venueId"] == venue


@pytest.mark.requirement("programme:R26")
@pytest.mark.asyncio
async def test_should_reject_in_place_reschedule_when_event_is_a_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)

    response = await client.post(
        f"/v1/events/by_id/{programme['id']}/reschedule",
        json={
            "version": await version_of(client, admin, programme["id"]),
            "startTimeUtc": start + HOUR_MS,
        },
        headers=auth(admin),
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "EVENT_TYPE_NOT_SUPPORTED"
    assert (await get_event(client, admin, programme["id"]))["startTimeUtc"] == start


# ---------------------------------------------------------------------------
# R34: audit
# ---------------------------------------------------------------------------


@pytest.mark.requirement("programme:R34")
@pytest.mark.asyncio
async def test_should_audit_changed_fields_when_programme_is_split_and_corrected(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    cutoff = start + 3 * DAY_MS

    done = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=other
    )
    assert done.status_code == 200, done.text
    fixed = await _correct(client, admin, programme["id"], title="Junior Squad")
    assert fixed.status_code == 200, fixed.text

    splits = await audit_rows(db_session, "split_event")
    assert len(splits) == 1
    assert splits[0].resource_id == str(programme["id"])
    assert f'"cutoff": {cutoff}' in splits[0].details
    assert '"venue_id"' in splits[0].details
    corrections = await audit_rows(db_session, "correct_event")
    assert len(corrections) == 1
    assert '"title"' in corrections[0].details
    assert '"description"' not in corrections[0].details


# ---------------------------------------------------------------------------
# #388: the schedule model
# ---------------------------------------------------------------------------


@pytest.mark.requirement("oneoff:R14a")
@pytest.mark.asyncio
async def test_should_give_every_event_one_schedule_when_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    camp = await create_camp(client, admin, venue, start=start, count=3)
    oneoff = await create_oneoff(
        client, admin, venue, start=start, end=start + 2 * HOUR_MS
    )

    programme_schedules = await _schedules(client, admin, programme["id"])
    camp_schedules = await _schedules(client, admin, camp["id"])
    oneoff_schedules = await _schedules(client, admin, oneoff["id"])

    assert [s["effectiveFromUtc"] for s in programme_schedules] == [start]
    assert programme_schedules[0]["effectiveUntilUtc"] is None
    assert [s["rrule"] for s in camp_schedules] == ["FREQ=DAILY;COUNT=3"]
    assert [s["rrule"] for s in oneoff_schedules] == [None]
    assert oneoff_schedules[0]["effectiveUntilUtc"] is None


@pytest.mark.requirement("programme:R24d")
@pytest.mark.asyncio
async def test_should_keep_overrides_and_attendance_where_they_are_when_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    cutoff = start + 3 * DAY_MS
    beyond = cutoff + 2 * DAY_MS
    cancelled = await client.post(
        f"/v1/events/by_id/{programme['id']}/occurrences/{beyond}/cancel",
        json={
            "version": await occurrence_version(client, admin, programme["id"], beyond),
            "reason": "Ice not ready",
        },
        headers=auth(admin),
    )
    assert cancelled.status_code == 204, cancelled.text
    leave = await client.post(
        f"/v1/myevents/by_id/skater/{programme['id']}/occurrences/{cutoff + DAY_MS}/leave/request",
        json={"reason": "Holiday"},
        headers=auth(admin),
    )
    assert leave.status_code == 204, leave.text

    done = await split(
        client, admin, programme["id"], effectiveDateTimeUtc=cutoff, venueId=other
    )
    assert done.status_code == 200, done.text

    listed = {
        o["occurrenceTimeUtc"]: o
        for o in await list_occurrences(
            client, admin, start - HOUR_MS, start + 7 * DAY_MS, event_id=programme["id"]
        )
    }
    assert listed[beyond]["status"] == "cancelled"
    record = await client.get(
        f"/v1/events/by_id/{programme['id']}/occurrences/{cutoff + DAY_MS}/attendance",
        headers=auth(admin),
    )
    assert record.status_code == 200, record.text
    assert [(r["membername"], r["status"]) for r in record.json()] == [
        ("skater", "onLeaveRequested")
    ]


@pytest.mark.requirement("lifecycle:L1")
@pytest.mark.asyncio
async def test_should_expose_no_chain_fields_when_event_is_read(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue)

    fetched = await get_event(client, admin, programme["id"])
    chain = await client.get(
        f"/v1/events/by_id/{programme['id']}/chain", headers=auth(admin)
    )

    assert "continuedAsEventId" not in fetched
    assert "continuedFromEventId" not in fetched
    assert chain.status_code == 404


@pytest.mark.requirement("lifecycle:L6")
@pytest.mark.asyncio
async def test_should_stay_scheduled_when_split_and_be_bounded_when_terminated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    other = await create_venue(client, admin, "Rink B")
    start = at(days=2)
    programme = await create_programme(client, admin, venue, start=start)
    done = await split(
        client,
        admin,
        programme["id"],
        effectiveDateTimeUtc=start + 3 * DAY_MS,
        venueId=other,
    )
    assert done.status_code == 200, done.text

    after_split = await get_event(client, admin, programme["id"])
    assert after_split["untilTimeUtc"] is None

    ended = await client.post(
        f"/v1/events/by_id/{programme['id']}/terminate",
        json={
            "reason": "Season over",
            "cutoffTimeUtc": start + 6 * DAY_MS,
            "version": after_split["version"],
        },
        headers=auth(admin),
    )
    assert ended.status_code == 200, ended.text
    after_terminate = await get_event(client, admin, programme["id"])
    assert after_terminate["untilTimeUtc"] == start + 6 * DAY_MS
