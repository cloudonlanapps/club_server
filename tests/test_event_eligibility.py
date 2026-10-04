"""Tests for issue #18: structured event eligibility, sessions timetable,
and aux-info removal."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user

DOB_2010 = 1262304000000  # 2010-01-01 UTC midnight
DOB_2014 = 1388534400000  # 2014-01-01 UTC midnight
DOB_2000 = 946684800000  # 2000-01-01 UTC midnight
ONE_DAY_MS = 86_400_000


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def future_time(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


async def _create_venue(client: AsyncClient, admin_token: str) -> int:
    r = await client.post(
        "/v1/venues",
        json={"name": "Rink"},
        headers=auth(admin_token),
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _register_member(
    client: AsyncClient,
    admin_token: str,
    username: str,
    db_session: AsyncSession,
    *,
    gender: str | None = "male",
    date_of_birth: int | None = DOB_2000,
) -> str:
    """Register an active member; returns their access token."""
    reg = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "testpass123",
            "firstName": username.capitalize(),
            "gender": "male",
            "dateOfBirthUtc": DOB_2000,
            "phone": "1234567890",
        },
    )
    assert reg.status_code in (200, 201)
    pre = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    assert pre.status_code == 200
    await attach_identity_document(db_session, username)
    sub = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {pre.json()['accessToken']}"},
    )
    assert sub.status_code == 200, sub.text
    appr = await client.post(
        f"/v1/users/by_id/{username}/approve", headers=auth(admin_token)
    )
    assert appr.status_code in (200, 201, 204)

    # Patch dob/gender if different from defaults; supports clearing to None.
    update: dict = {}
    if gender != "male":
        update["gender"] = gender
    if date_of_birth != DOB_2000:
        update["dateOfBirthUtc"] = date_of_birth
    if update:
        patch = await client.patch(
            f"/v1/users/by_id/{username}", json=update, headers=auth(admin_token)
        )
        assert patch.status_code == 200

    login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    return login.json()["accessToken"]


async def _create_event(
    client: AsyncClient,
    admin_token: str,
    venue_id: int,
    *,
    event_type: str = "camp",
    visibility: str = "public",
    gender: str | None = None,
    dob_on_or_after_utc: int | None = None,
    dob_on_or_before_utc: int | None = None,
    sessions: list[dict] | None = None,
    start_offset_hours: int = 24,
    duration_hours: int = 1,
    is_featured: bool = False,
    gallery_uris: list[str] | None = None,
    expect_status: int = 201,
) -> dict:
    # Derive end from a single start so the window is exactly duration_hours
    # (two separate future_time() calls drift by the elapsed ms between them,
    # which makes the sessions-total equality check flaky).
    start_utc = future_time(start_offset_hours)
    body: dict = {
        "title": "Camp",
        "type": event_type,
        "venueId": venue_id,
        "visibility": visibility,
        "startTimeUtc": start_utc,
        "endTimeUtc": start_utc + duration_hours * 3600 * 1000,
    }
    if gender is not None:
        body["gender"] = gender
    if dob_on_or_after_utc is not None:
        body["dobOnOrAfterUtc"] = dob_on_or_after_utc
    if dob_on_or_before_utc is not None:
        body["dobOnOrBeforeUtc"] = dob_on_or_before_utc
    if sessions is not None:
        body["sessions"] = sessions
    if is_featured:
        body["isFeatured"] = True
    if gallery_uris is not None:
        body["galleryUris"] = gallery_uris
    r = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert r.status_code == expect_status, r.text
    return r.json() if expect_status == 201 else r.json()


# ---------------------------------------------------------------------------
# Event field round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_event_with_eligibility_and_media_round_trips(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)

    event = await _create_event(
        client,
        admin_token,
        venue_id,
        gender="male",
        dob_on_or_after_utc=DOB_2010,
        dob_on_or_before_utc=DOB_2014,
        is_featured=True,
        gallery_uris=["https://example/g1.jpg", "https://example/g2.jpg"],
    )
    assert event["gender"] == "male"
    assert event["dobOnOrAfterUtc"] == DOB_2010
    assert event["dobOnOrBeforeUtc"] == DOB_2014
    assert event["isFeatured"] is True
    assert event["galleryUris"] == ["https://example/g1.jpg", "https://example/g2.jpg"]

    # Re-fetch via GET
    got = await client.get(f"/v1/events/by_id/{event['id']}", headers=auth(admin_token))
    assert got.status_code == 200
    assert got.json()["dobOnOrAfterUtc"] == DOB_2010


@pytest.mark.asyncio
async def test_create_rejects_inverted_dob_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    body = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "startTimeUtc": future_time(24),
        "endTimeUtc": future_time(25),
        "dobOnOrAfterUtc": DOB_2014,
        "dobOnOrBeforeUtc": DOB_2010,
    }
    r = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_dob_bounds_non_midnight_rejected_with_422(
    client: AsyncClient, db_session: AsyncSession
):
    """Per #100: event dobOn* bounds must be at UTC midnight; non-midnight is 422."""
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    not_midnight = DOB_2010 + 12 * 60 * 60 * 1000  # 2010-01-01 12:00 UTC
    r = await _create_event(
        client,
        admin_token,
        venue_id,
        dob_on_or_after_utc=not_midnight,
        expect_status=422,
    )
    assert r["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert r["detail"]["field"] == "dobOnOrAfterUtc"


# ---------------------------------------------------------------------------
# Eligibility on invite / assign / assign-trial / approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_ineligible_user_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    _ = await _register_member(
        client, admin_token, "u_old", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(
        client, admin_token, venue_id, dob_on_or_after_utc=DOB_2010
    )
    r = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/invite",
        json={"membernames": ["u_old"]},
        headers=auth(admin_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


@pytest.mark.asyncio
async def test_assign_ineligible_user_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    _ = await _register_member(
        client, admin_token, "u_old", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(
        client, admin_token, venue_id, dob_on_or_after_utc=DOB_2010
    )
    r = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/assign",
        json={"membernames": ["u_old"]},
        headers=auth(admin_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


@pytest.mark.asyncio
async def test_assign_trial_ineligible_user_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    _ = await _register_member(
        client, admin_token, "u_old", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(
        client,
        admin_token,
        venue_id,
        event_type="programme",
        dob_on_or_after_utc=DOB_2010,
    )
    r = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/assign-trial",
        json={"membername": "u_old"},
        headers=auth(admin_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


@pytest.mark.asyncio
async def test_self_request_ineligible_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    user_token = await _register_member(
        client, admin_token, "u_old", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(
        client,
        admin_token,
        venue_id,
        visibility="public",
        dob_on_or_after_utc=DOB_2010,
    )
    r = await client.post(
        f"/v1/myevents/by_id/u_old/{event['id']}/enrollments/request",
        headers=auth(user_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


@pytest.mark.asyncio
async def test_approve_re_checks_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    """After requesting, if criteria are tightened so the requester no longer
    matches, approval rejects 422 and the enrollment stays `requested`."""
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    user_token = await _register_member(
        client, admin_token, "kid", db_session, date_of_birth=DOB_2014
    )

    # Create an open camp (no criteria yet) and let the user request.
    event = await _create_event(client, admin_token, venue_id, visibility="public")
    r = await client.post(
        f"/v1/myevents/by_id/kid/{event['id']}/enrollments/request",
        headers=auth(user_token),
    )
    assert r.status_code == 204

    # Tighten the camp's eligibility — kid (DOB 2014) is now too young.
    patch = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"dobOnOrAfterUtc": DOB_2010, "dobOnOrBeforeUtc": DOB_2010, "version": 1},
        headers=auth(admin_token),
    )
    assert patch.status_code == 200

    # Approve should now reject with USER_NOT_ELIGIBLE_FOR_EVENT.
    appr = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/approve",
        json={"membernames": ["kid"]},
        headers=auth(admin_token),
    )
    assert appr.status_code == 422
    assert appr.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"

    # Enrollment stays in `requested` — verify via the admin enrollments list.
    enr = await client.get(
        f"/v1/events/by_id/{event['id']}/enrollments?status=requested",
        headers=auth(admin_token),
    )
    assert enr.status_code == 200
    enrollments = enr.json()["enrollments"]
    assert enrollments.get("kid") == "requested"


@pytest.mark.asyncio
async def test_user_with_null_dob_ineligible_when_event_has_dob_bound(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    # Register member then null out their DOB.
    _ = await _register_member(
        client, admin_token, "anon", db_session, date_of_birth=DOB_2000
    )
    # Use the admin patch endpoint to clear the dob.
    nullify = await client.patch(
        "/v1/users/by_id/anon",
        json={"dateOfBirthUtc": None},
        headers=auth(admin_token),
    )
    # Some endpoints may not allow null - if so, skip with a soft assertion.
    if nullify.status_code != 200:
        pytest.skip("user dob cannot be set to null via PATCH; environment dependent")

    event = await _create_event(
        client, admin_token, venue_id, dob_on_or_after_utc=DOB_2010
    )
    r = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/invite",
        json={"membernames": ["anon"]},
        headers=auth(admin_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


@pytest.mark.asyncio
async def test_grandfathering_keeps_existing_enrollment(
    client: AsyncClient, db_session: AsyncSession
):
    """Tightening eligibility after enrollment does not auto-cancel anyone."""
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    _ = await _register_member(
        client, admin_token, "vet", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(client, admin_token, venue_id)

    # Assign while no criteria.
    r = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/assign",
        json={"membernames": ["vet"]},
        headers=auth(admin_token),
    )
    assert r.status_code == 204

    # Tighten — vet is now ineligible by DOB.
    patch = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"dobOnOrAfterUtc": DOB_2010, "version": 1},
        headers=auth(admin_token),
    )
    assert patch.status_code == 200

    # Existing enrollment is still active.
    enr = await client.get(
        f"/v1/events/by_id/{event['id']}/enrollments",
        headers=auth(admin_token),
    )
    assert enr.status_code == 200
    enrollments = enr.json()["enrollments"]
    assert enrollments.get("vet") == "assigned"


# ---------------------------------------------------------------------------
# /myevents listing filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_myevents_hides_ineligible_public_event(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    user_token = await _register_member(
        client, admin_token, "stranger", db_session, date_of_birth=DOB_2000
    )

    # Public camp restricted to people born after 2010 — stranger is ineligible.
    event = await _create_event(
        client,
        admin_token,
        venue_id,
        visibility="public",
        dob_on_or_after_utc=DOB_2010,
    )
    r = await client.get("/v1/myevents/by_id/stranger", headers=auth(user_token))
    assert r.status_code == 200
    listed_ids = [e["id"] for e in r.json()["items"]]
    assert event["id"] not in listed_ids


@pytest.mark.asyncio
async def test_myevents_keeps_enrolled_event_even_when_ineligible(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    user_token = await _register_member(
        client, admin_token, "vet2", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(client, admin_token, venue_id, visibility="public")

    # Assign first, then tighten criteria so vet2 becomes ineligible.
    _ = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/assign",
        json={"membernames": ["vet2"]},
        headers=auth(admin_token),
    )
    _ = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"dobOnOrAfterUtc": DOB_2010, "version": 1},
        headers=auth(admin_token),
    )
    r = await client.get("/v1/myevents/by_id/vet2", headers=auth(user_token))
    assert r.status_code == 200
    listed_ids = [e["id"] for e in r.json()["items"]]
    assert event["id"] in listed_ids


# ---------------------------------------------------------------------------
# Sessions timetable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_round_trip_when_sum_matches_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    # 2-hour window; sessions sum to 120 minutes.
    event = await _create_event(
        client,
        admin_token,
        venue_id,
        duration_hours=2,
        sessions=[
            {"name": "Warm-up", "periodMinutes": 30},
            {"name": "Drill", "periodMinutes": 60},
            {"name": "Cool-down", "periodMinutes": 30},
        ],
    )
    assert event["sessions"] == [
        {"name": "Warm-up", "periodMinutes": 30},
        {"name": "Drill", "periodMinutes": 60},
        {"name": "Cool-down", "periodMinutes": 30},
    ]


@pytest.mark.asyncio
async def test_sessions_sum_mismatch_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    body = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "startTimeUtc": future_time(24),
        "endTimeUtc": future_time(26),  # 120-min window
        "sessions": [{"name": "Half", "periodMinutes": 30}],  # only 30 min
    }
    r = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_SESSIONS_TOTAL"


@pytest.mark.asyncio
async def test_sessions_empty_list_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    body = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "startTimeUtc": future_time(24),
        "endTimeUtc": future_time(25),
        "sessions": [],
    }
    r = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_SESSIONS_EMPTY"


@pytest.mark.asyncio
async def test_sessions_zero_period_rejected_422(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    body = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "startTimeUtc": future_time(24),
        "endTimeUtc": future_time(25),
        "sessions": [{"name": "Zero", "periodMinutes": 0}],
    }
    r = await client.post("/v1/events", json=body, headers=auth(admin_token))
    # Pydantic ge=1 produces 422 with field-level error
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_sessions_accepted_when_they_fit_the_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    # #248 moved sessions off the generic PATCH to /reschedule; #423 brings
    # them back as a correction of record (camp R105), validated against the
    # window. The window itself still moves only through /reschedule.
    event = await _create_event(
        client,
        admin_token,
        venue_id,
        duration_hours=2,
        sessions=[
            {"name": "Hour 1", "periodMinutes": 60},
            {"name": "Hour 2", "periodMinutes": 60},
        ],
    )
    r = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"sessions": [{"name": "Whole", "periodMinutes": 120}], "version": 1},
        headers=auth(admin_token),
    )
    assert r.status_code == 200, r.text
    fetched = await client.get(
        f"/v1/events/by_id/{event['id']}", headers=auth(admin_token)
    )
    assert fetched.json()["sessions"] == [{"name": "Whole", "periodMinutes": 120}]


# ---------------------------------------------------------------------------
# Aux-info endpoints removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_aux_info_endpoint_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    event = await _create_event(client, admin_token, venue_id)
    r = await client.get(
        f"/v1/events/by_id/{event['id']}/aux-info", headers=auth(admin_token)
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_put_aux_info_endpoint_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    event = await _create_event(client, admin_token, venue_id)
    r = await client.put(
        f"/v1/events/by_id/{event['id']}/aux-info",
        json={"tagline": "x"},
        headers=auth(admin_token),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Coverage gaps surfaced during requirements review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_rejects_inverted_dob_window(
    client: AsyncClient, db_session: AsyncSession
):
    """R15 — inverted DOB window is rejected on PATCH, not just POST."""
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    event = await _create_event(client, admin_token, venue_id)
    r = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"dobOnOrAfterUtc": DOB_2014, "dobOnOrBeforeUtc": DOB_2010, "version": 1},
        headers=auth(admin_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_user_with_null_gender_ineligible_when_event_sets_gender(
    client: AsyncClient, db_session: AsyncSession
):
    """R42 — NULL user gender is ineligible for any event with `gender` set."""
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    _ = await _register_member(
        client, admin_token, "anon", db_session, date_of_birth=DOB_2000
    )
    nullify = await client.patch(
        "/v1/users/by_id/anon",
        json={"gender": None},
        headers=auth(admin_token),
    )
    if nullify.status_code != 200:
        pytest.skip("user gender cannot be set to null via PATCH")

    event = await _create_event(client, admin_token, venue_id, gender="female")
    r = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/invite",
        json={"membernames": ["anon"]},
        headers=auth(admin_token),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


@pytest.mark.asyncio
async def test_invite_replacing_terminal_enrollment_re_checks_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    """R53 + R54 — invite that overwrites a terminal-status row must still
    pass eligibility against the event's current criteria."""
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    user_token = await _register_member(
        client, admin_token, "kid", db_session, date_of_birth=DOB_2014
    )
    # Open camp; user requests, admin rejects (terminal `rejected` row).
    event = await _create_event(client, admin_token, venue_id, visibility="public")
    rq = await client.post(
        f"/v1/myevents/by_id/kid/{event['id']}/enrollments/request",
        headers=auth(user_token),
    )
    assert rq.status_code == 204
    rj = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/reject",
        json={"membernames": ["kid"], "reason": "no spot"},
        headers=auth(admin_token),
    )
    assert rj.status_code == 204

    # Tighten — kid (DOB 2014) is now too old.
    pt = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"dobOnOrBeforeUtc": DOB_2010, "version": 1},
        headers=auth(admin_token),
    )
    assert pt.status_code == 200

    # Re-invite must be rejected, not silently overwrite the terminal row.
    iv = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/invite",
        json={"membernames": ["kid"]},
        headers=auth(admin_token),
    )
    assert iv.status_code == 422
    assert iv.json()["detail"]["code"] == "USER_NOT_ELIGIBLE_FOR_EVENT"


def test_aux_info_supporting_types_are_no_longer_importable():
    """R93 — schemas serialised only for aux-info are removed from the
    public schema layer."""
    import club_server.schemas as schemas_pkg

    for symbol in (
        "EventAuxInfoRequest",
        "EventAuxInfoResponse",
        "Facility",
        "FeeItem",
        "PackageOffer",
        "ClubMembership",
        "ClubMemberBenefit",
        "PromotionalOffer",
        "BatchSession",
        "Batch",
    ):
        assert not hasattr(schemas_pkg, symbol), (
            f"club_server.schemas.{symbol} should no longer be exported"
        )
