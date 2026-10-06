"""Age-based eligibility on events (#16, eligibility_requirements.md)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_day, club_today
from club_server.utils import MS_PER_DAY

from .eligibility_helpers import EIGHTEEN, FIVE, edge_births, window_on
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    EVERY_DAY,
    assign,
    at,
    auth,
    cancel_occurrence,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    enrollment_of,
    get_event,
    terminate,
    version_of,
)

BAND = {"minAge": FIVE, "maxAge": EIGHTEEN}
WINDOW_FIELDS = ("dobOnOrAfterUtc", "dobOnOrBeforeUtc", "eligibilityReferenceDayUtc")


def reported_window(event: dict) -> tuple[int | None, int | None, int]:
    return (
        event["dobOnOrAfterUtc"],
        event["dobOnOrBeforeUtc"],
        event["eligibilityReferenceDayUtc"],
    )


def expected_window(reference_day: int, *, strict: bool) -> tuple[int, int, int]:
    window = window_on(reference_day, strict=strict)
    assert window.dob_on_or_after_utc is not None
    assert window.dob_on_or_before_utc is not None
    return (window.dob_on_or_after_utc, window.dob_on_or_before_utc, reference_day)


async def setup(client: AsyncClient, db_session: AsyncSession) -> tuple[str, int]:
    admin = await create_admin_user(db_session)
    return admin, await create_venue(client, admin)


# --- R1, R2, R10, R11: storing the band -----------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R1")
async def test_should_store_the_age_band_when_an_event_is_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)

    created = await create_camp(client, admin, venue, **BAND, strictAge=True)

    fetched = await get_event(client, admin, created["id"])
    assert fetched["minAge"] == FIVE
    assert fetched["maxAge"] == EIGHTEEN
    assert fetched["strictAge"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R1")
async def test_should_default_to_a_relaxed_check_and_no_bounds(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)

    created = await create_oneoff(client, admin, venue)

    fetched = await get_event(client, admin, created["id"])
    assert fetched["minAge"] is None
    assert fetched["maxAge"] is None
    assert fetched["strictAge"] is False
    assert fetched["dobOnOrAfterUtc"] is None
    assert fetched["dobOnOrBeforeUtc"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R1")
async def test_should_clear_a_bound_on_null_and_keep_an_omitted_one_when_updating(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    camp = await create_camp(client, admin, venue, **BAND, strictAge=True)

    patched = await client.patch(
        f"/v1/events/by_id/{camp['id']}",
        json={"minAge": None, "version": await version_of(client, admin, camp["id"])},
        headers=auth(admin),
    )
    assert patched.status_code == 200, patched.text

    fetched = await get_event(client, admin, camp["id"])
    assert fetched["minAge"] is None
    assert fetched["maxAge"] == EIGHTEEN
    assert fetched["strictAge"] is True
    assert fetched["dobOnOrBeforeUtc"] is None
    assert fetched["dobOnOrAfterUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R1")
async def test_should_set_the_age_band_of_a_programme_by_correction(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    programme = await create_programme(client, admin, venue)

    corrected = await client.patch(
        f"/v1/events/by_id/{programme['id']}/correction",
        json={
            "maxAge": {"years": 12, "months": 6},
            "strictAge": True,
            "version": await version_of(client, admin, programme["id"]),
        },
        headers=auth(admin),
    )
    assert corrected.status_code == 200, corrected.text

    fetched = await get_event(client, admin, programme["id"])
    assert fetched["maxAge"] == {"years": 12, "months": 6, "days": 0}
    assert fetched["minAge"] is None
    assert fetched["strictAge"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R2")
@pytest.mark.parametrize(
    "age", [{"years": 5, "months": 12}, {"years": -1}, {"months": 3}, 5, "5"]
)
async def test_should_refuse_an_event_whose_age_is_malformed(
    client: AsyncClient, db_session: AsyncSession, age: object
):
    admin, venue = await setup(client, db_session)
    await db_session.commit()

    _ = await create_camp(client, admin, venue, minAge=age, expected_status=422)

    listing = await client.get("/v1/events", headers=auth(admin))
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R10")
async def test_should_refuse_an_event_whose_minimum_age_exceeds_its_maximum(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    await db_session.commit()

    _ = await create_camp(
        client, admin, venue, minAge=EIGHTEEN, maxAge=FIVE, expected_status=422
    )

    listing = await client.get("/v1/events", headers=auth(admin))
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R10")
async def test_should_refuse_an_update_that_inverts_the_band_and_change_nothing(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    camp = await create_camp(client, admin, venue, **BAND)

    patched = await client.patch(
        f"/v1/events/by_id/{camp['id']}",
        json={
            "minAge": {"years": 19},
            "version": await version_of(client, admin, camp["id"]),
        },
        headers=auth(admin),
    )
    assert patched.status_code == 422, patched.text

    fetched = await get_event(client, admin, camp["id"])
    assert fetched["minAge"] == FIVE
    assert fetched["maxAge"] == EIGHTEEN


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R11")
@pytest.mark.parametrize("field", ["dobOnOrAfterUtc", "dobOnOrBeforeUtc"])
async def test_should_refuse_a_date_of_birth_bound_when_creating_an_event(
    client: AsyncClient, db_session: AsyncSession, field: str
):
    admin, venue = await setup(client, db_session)
    await db_session.commit()

    _ = await create_camp(
        client, admin, venue, expected_status=422, **{field: 1262304000000}
    )

    listing = await client.get("/v1/events", headers=auth(admin))
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R11")
@pytest.mark.parametrize("field", ["dobOnOrAfterUtc", "dobOnOrBeforeUtc"])
async def test_should_refuse_a_date_of_birth_bound_when_updating_or_correcting(
    client: AsyncClient, db_session: AsyncSession, field: str
):
    admin, venue = await setup(client, db_session)
    camp = await create_camp(client, admin, venue, **BAND)
    programme = await create_programme(client, admin, venue, start=at(days=3))

    updated = await client.patch(
        f"/v1/events/by_id/{camp['id']}",
        json={field: 1262304000000, "version": camp["version"]},
        headers=auth(admin),
    )
    assert updated.status_code == 422, updated.text
    corrected = await client.patch(
        f"/v1/events/by_id/{programme['id']}/correction",
        json={field: 1262304000000, "version": programme["version"]},
        headers=auth(admin),
    )
    assert corrected.status_code == 422, corrected.text

    assert (await get_event(client, admin, camp["id"]))["minAge"] == FIVE


# --- R4, R4a, R4b, R12: the reference day and what reports it -------------


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4")
@pytest.mark.parametrize("strict", [True, False])
async def test_should_count_a_camp_from_the_day_it_starts(
    client: AsyncClient, db_session: AsyncSession, strict: bool
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)

    camp = await create_camp(
        client, admin, venue, start=start, **BAND, strictAge=strict
    )

    fetched = await get_event(client, admin, camp["id"])
    assert reported_window(fetched) == expected_window(club_day(start), strict=strict)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4")
async def test_should_count_a_one_off_from_the_day_it_starts(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=45)

    oneoff = await create_oneoff(
        client, admin, venue, start=start, **BAND, strictAge=True
    )

    fetched = await get_event(client, admin, oneoff["id"])
    assert reported_window(fetched) == expected_window(club_day(start), strict=True)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4a")
async def test_should_move_a_camps_window_when_its_start_day_is_changed(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    camp = await create_camp(client, admin, venue, start=start, **BAND, strictAge=True)
    later = start + 40 * DAY_MS

    moved = await client.post(
        f"/v1/events/by_id/{camp['id']}/reschedule",
        json={
            "startTimeUtc": later,
            "endTimeUtc": later + (camp["endTimeUtc"] - camp["startTimeUtc"]),
            "version": await version_of(client, admin, camp["id"]),
        },
        headers=auth(admin),
    )
    assert moved.status_code == 200, moved.text

    fetched = await get_event(client, admin, camp["id"])
    assert reported_window(fetched) == expected_window(club_day(later), strict=True)
    assert fetched["eligibilityReferenceDayUtc"] != club_day(start)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4b")
async def test_should_count_a_programme_from_its_next_occurrence(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=10)

    programme = await create_programme(
        client, admin, venue, start=start, rrule=EVERY_DAY, **BAND, strictAge=True
    )

    fetched = await get_event(client, admin, programme["id"])
    assert reported_window(fetched) == expected_window(club_day(start), strict=True)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4b")
async def test_should_skip_a_cancelled_occurrence_when_counting_a_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=10)
    programme = await create_programme(
        client, admin, venue, start=start, rrule=EVERY_DAY, **BAND, strictAge=True
    )

    cancelled = await cancel_occurrence(client, admin, programme["id"], start)
    assert cancelled.status_code in (200, 204), cancelled.text

    fetched = await get_event(client, admin, programme["id"])
    assert fetched["eligibilityReferenceDayUtc"] == club_day(start + DAY_MS)
    assert reported_window(fetched) == expected_window(
        club_day(start + DAY_MS), strict=True
    )


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4b")
async def test_should_count_a_programme_from_today_when_it_has_no_occurrence_left(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=10)
    programme = await create_programme(
        client, admin, venue, start=start, rrule=EVERY_DAY, **BAND, strictAge=True
    )

    ended = await terminate(client, admin, programme["id"], start)
    assert ended.status_code == 200, ended.text

    fetched = await get_event(client, admin, programme["id"])
    assert fetched["eligibilityReferenceDayUtc"] == club_today()


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R12")
async def test_should_report_band_window_and_reference_day_to_staff_member_and_public(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    window = window_on(club_day(start), strict=False)
    member = await create_member_user(
        db_session, "alice", date_of_birth=window.dob_on_or_after_utc
    )
    camp = await create_camp(
        client, admin, venue, start=start, visibility="public", **BAND
    )
    expected = expected_window(club_day(start), strict=False)

    as_staff = await get_event(client, admin, camp["id"])
    as_member = await client.get(
        f"/v1/myevents/by_id/alice/{camp['id']}", headers=auth(member)
    )
    assert as_member.status_code == 200, as_member.text
    public = await client.get("/v1/public/events")
    assert public.status_code == 200, public.text
    as_public = public.json()["items"][0]

    for view in (as_staff, as_member.json(), as_public):
        assert view["minAge"] == FIVE
        assert view["maxAge"] == EIGHTEEN
        assert view["strictAge"] is False
        assert reported_window(view) == expected


# --- R13, R14, R15: what checks the window --------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R13")
@pytest.mark.parametrize("strict", [True, False])
async def test_should_assign_only_members_inside_the_window_at_both_ends(
    client: AsyncClient, db_session: AsyncSession, strict: bool
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    births = edge_births(window_on(club_day(start), strict=strict))
    for name, born in births.items():
        _ = await create_member_user(db_session, name, date_of_birth=born)
    camp = await create_camp(
        client, admin, venue, start=start, **BAND, strictAge=strict
    )
    await db_session.commit()

    for name in ("oldest", "youngest"):
        accepted = await assign(client, admin, camp["id"], name)
        assert accepted.status_code == 204, (name, accepted.text)
        assert await enrollment_of(client, admin, camp["id"], name) == "assigned"
    for name in ("too_old", "too_young"):
        refused = await assign(client, admin, camp["id"], name)
        assert refused.status_code == 422, (name, refused.text)
        assert refused.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"
        assert await enrollment_of(client, admin, camp["id"], name) is None


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R13")
async def test_should_accept_a_request_inside_the_window_and_refuse_one_outside(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    births = edge_births(window_on(club_day(start), strict=False))
    inside = await create_member_user(
        db_session, "inside", date_of_birth=births["youngest"]
    )
    outside = await create_member_user(
        db_session, "outside", date_of_birth=births["too_young"]
    )
    camp = await create_camp(
        client, admin, venue, start=start, visibility="public", **BAND
    )
    await db_session.commit()
    url = "/v1/myevents/by_id/{}/" + f"{camp['id']}/enrollments/request"

    accepted = await client.post(url.format("inside"), headers=auth(inside))
    assert accepted.status_code in (200, 201, 204), accepted.text
    assert await enrollment_of(client, admin, camp["id"], "inside") == "requested"

    refused = await client.post(url.format("outside"), headers=auth(outside))
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"
    assert await enrollment_of(client, admin, camp["id"], "outside") is None


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R13")
async def test_should_refuse_an_invite_and_an_approval_outside_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    births = edge_births(window_on(club_day(start), strict=True))
    _ = await create_member_user(db_session, "old", date_of_birth=births["too_old"])
    waiting = await create_member_user(
        db_session, "waiting", date_of_birth=births["oldest"]
    )
    camp = await create_camp(
        client, admin, venue, start=start, visibility="public", **BAND, strictAge=True
    )
    requested = await client.post(
        f"/v1/myevents/by_id/waiting/{camp['id']}/enrollments/request",
        headers=auth(waiting),
    )
    assert requested.status_code in (200, 201, 204), requested.text
    # The band tightens after the request: the requester is now too old.
    tightened = await client.patch(
        f"/v1/events/by_id/{camp['id']}",
        json={
            "maxAge": {"years": 17},
            "version": await version_of(client, admin, camp["id"]),
        },
        headers=auth(admin),
    )
    assert tightened.status_code == 200, tightened.text

    invited = await client.post(
        f"/v1/events/by_id/{camp['id']}/enrollments/invite",
        json={"membernames": ["old"]},
        headers=auth(admin),
    )
    assert invited.status_code == 422, invited.text
    assert invited.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"
    approved = await client.post(
        f"/v1/events/by_id/{camp['id']}/enrollments/approve",
        json={"membernames": ["waiting"]},
        headers=auth(admin),
    )
    assert approved.status_code == 422, approved.text
    assert approved.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"

    assert await enrollment_of(client, admin, camp["id"], "old") is None
    assert await enrollment_of(client, admin, camp["id"], "waiting") == "requested"


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R14")
async def test_should_list_as_eligible_only_users_inside_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    births = edge_births(window_on(club_day(start), strict=False))
    for name, born in births.items():
        _ = await create_member_user(db_session, name, date_of_birth=born)
    _ = await create_member_user(db_session, "undated")
    camp = await create_camp(client, admin, venue, start=start, **BAND)

    eligible = await client.get(
        f"/v1/events/by_id/{camp['id']}/eligible", headers=auth(admin)
    )

    assert eligible.status_code == 200, eligible.text
    names = {u["username"] for u in eligible.json()}
    assert {"oldest", "youngest"} <= names
    assert not names & {"too_old", "too_young", "undated"}


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R15")
async def test_should_leave_a_public_event_out_of_the_listing_of_a_member_outside_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin, venue = await setup(client, db_session)
    start = at(days=30)
    births = edge_births(window_on(club_day(start), strict=True))
    inside = await create_member_user(
        db_session, "inside", date_of_birth=births["oldest"]
    )
    outside = await create_member_user(
        db_session, "outside", date_of_birth=births["too_old"]
    )
    camp = await create_camp(
        client, admin, venue, start=start, visibility="public", **BAND, strictAge=True
    )

    sees = await client.get("/v1/myevents/by_id/inside", headers=auth(inside))
    hidden = await client.get("/v1/myevents/by_id/outside", headers=auth(outside))

    assert sees.status_code == 200, sees.text
    assert [e["id"] for e in sees.json()["items"]] == [camp["id"]]
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["items"] == []
    assert births["too_old"] + MS_PER_DAY == births["oldest"]
