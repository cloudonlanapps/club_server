"""API-level tests for the camp conflict endpoints introduced in Step D of #16.

- ``POST /v1/events/check-conflict``
- ``POST /v1/events/by_id/{id}/check-user-conflicts``

Events are seeded directly via the ORM (Step E loosens camp-create which is
needed for the HTTP creation path to succeed for overlapping camps — until
then we cannot rely on the HTTP create path to set up these fixtures).
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.enrollment import Enrollment, EnrollmentStatus
from club_server.db.models.event import Event
from club_server.db.models.user import User, UserStatus
from club_server.db.models.venue import Venue
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_coach_user, create_member_user


HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS
BASE_TS = 1_700_000_000_000


async def _mk_venue(db: AsyncSession, name: str = "Arena") -> int:
    venue = Venue(name=name, created_at=now_utc_ms(), updated_at=now_utc_ms())
    db.add(venue)
    await db.flush()
    return venue.id


async def _mk_camp(
    db: AsyncSession,
    *,
    venue_id: int,
    title: str = "Existing Camp",
    start_time: int = BASE_TS,
    rrule: str | None = "FREQ=DAILY;COUNT=3",
    organizer_name: str | None = None,
    coach_names: list[str] | None = None,
    type_: str = "camp",
    until_time: int | None = None,
) -> Event:
    for name in [organizer_name, *(coach_names or [])]:
        if name and await db.get(User, name) is None:
            _ = await create_member_user(db, name)
    event = Event(
        title=title,
        type=type_,
        visibility="public",
        venue_id=venue_id,
        organizer_name=organizer_name,
        coach_names=coach_names,
        rrule=rrule,
        start_time=start_time,
        end_time=start_time + 2 * HOUR_MS,
        until_time=until_time,
        is_featured=False,
        created_at=now_utc_ms(),
        updated_at=now_utc_ms(),
    )
    db.add(event)
    await db.flush()
    return event


def _camp_body(
    venue_id: int,
    *,
    type_: str = "camp",
    rrule: str | None = "FREQ=DAILY;COUNT=3",
    organizer_name: str | None = None,
    coach_names: list[str] | None = None,
    exclude_event_id: int | None = None,
) -> dict:
    body: dict = {
        "type": type_,
        "venueId": venue_id,
        "startTimeUtc": BASE_TS,
        "endTimeUtc": BASE_TS + 2 * HOUR_MS,
    }
    if rrule is not None:
        body["rrule"] = rrule
    if organizer_name is not None:
        body["organizerName"] = organizer_name
    if coach_names is not None:
        body["coachNames"] = coach_names
    if exclude_event_id is not None:
        body["excludeEventId"] = exclude_event_id
    return body


# ---------------------------------------------------------------------------
# POST /v1/events/check-conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_conflict_venue_overlap(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    existing = await _mk_camp(db_session, venue_id=venue_id)
    existing_id = existing.id

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["venueConflicts"]) == 1
    assert data["venueConflicts"][0]["eventId"] == existing_id
    assert len(data["venueConflicts"][0]["occurrences"]) == 3
    assert data["organizerConflicts"] == []
    assert data["coachConflicts"] == []


@pytest.mark.asyncio
async def test_check_conflict_organizer_overlap(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    other_venue = await _mk_venue(db_session, "Other")
    existing = await _mk_camp(db_session, venue_id=other_venue, organizer_name="alice")
    existing_id = existing.id

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id, organizer_name="alice"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["venueConflicts"] == []
    assert len(data["organizerConflicts"]) == 1
    assert data["organizerConflicts"][0]["eventId"] == existing_id


@pytest.mark.asyncio
async def test_check_conflict_coach_overlap(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    other_venue = await _mk_venue(db_session, "Other")
    existing = await _mk_camp(
        db_session, venue_id=other_venue, coach_names=["bob", "carol"]
    )
    existing_id = existing.id

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id, coach_names=["carol", "dan"]),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["coachConflicts"]) == 1
    assert data["coachConflicts"][0]["eventId"] == existing_id


@pytest.mark.asyncio
async def test_check_conflict_exclude_event_id_omits_self(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    existing = await _mk_camp(db_session, venue_id=venue_id)
    existing_id = existing.id

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id, exclude_event_id=existing_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["venueConflicts"] == []


@pytest.mark.asyncio
async def test_check_conflict_validates_programme_rule(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id, type_="programme"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_PROGRAMME"


@pytest.mark.asyncio
async def test_check_conflict_rejects_invalid_camp_rrule(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id, rrule="FREQ=WEEKLY;COUNT=5"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_CAMP"


@pytest.mark.asyncio
async def test_check_conflict_rejects_unbounded_camp_rrule(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id, rrule="FREQ=DAILY"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RRULE_FOR_CAMP"


@pytest.mark.asyncio
async def test_check_conflict_requires_auth(client: AsyncClient):
    response = await client.post(
        "/v1/events/check-conflict",
        json={
            "type": "camp",
            "venueId": 1,
            "startTimeUtc": BASE_TS,
            "endTimeUtc": BASE_TS + HOUR_MS,
            "rrule": "FREQ=DAILY;COUNT=3",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_check_conflict_rejects_member_role(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_member_user(db_session, "joe")
    venue_id = await _mk_venue(db_session)

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_check_conflict_allows_coach_role(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_coach_user(db_session, "coach1")
    venue_id = await _mk_venue(db_session)

    response = await client.post(
        "/v1/events/check-conflict",
        json=_camp_body(venue_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /v1/events/by_id/{event_id}/check-user-conflicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_user_conflicts_overlap(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    other_venue = await _mk_venue(db_session, "Other")
    target = await _mk_camp(db_session, venue_id=venue_id, title="Target Camp")
    other = await _mk_camp(db_session, venue_id=other_venue, title="Other Camp")
    target_id = target.id
    other_id = other.id

    # Create member and enroll in `other`
    member = User(
        username="m1",
        password=AuthService.hash_password("pw"),
        first_name="M1",
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        created_at=now_utc_ms(),
    )
    db_session.add(member)
    await db_session.flush()
    db_session.add(
        Enrollment(
            membername="m1",
            event_id=other_id,
            status=EnrollmentStatus.accepted.value,
            created_at=now_utc_ms(),
        )
    )
    await db_session.flush()

    response = await client.post(
        f"/v1/events/by_id/{target_id}/check-user-conflicts",
        json={"usernames": ["m1"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["userConflicts"]) == 1
    assert data["userConflicts"][0]["username"] == "m1"
    assert data["userConflicts"][0]["events"][0]["eventId"] == other_id


@pytest.mark.asyncio
async def test_check_user_conflicts_no_conflict_returns_empty(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    target = await _mk_camp(db_session, venue_id=venue_id)
    target_id = target.id

    response = await client.post(
        f"/v1/events/by_id/{target_id}/check-user-conflicts",
        json={"usernames": ["nobody"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["userConflicts"] == []


@pytest.mark.asyncio
async def test_check_user_conflicts_accepts_programme(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    venue_id = await _mk_venue(db_session)
    programme = await _mk_camp(
        db_session, venue_id=venue_id, type_="programme", rrule=None
    )
    programme_id = programme.id

    response = await client.post(
        f"/v1/events/by_id/{programme_id}/check-user-conflicts",
        json={"usernames": ["nobody"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["userConflicts"] == []


@pytest.mark.asyncio
async def test_check_user_conflicts_404_for_missing_event(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/events/by_id/999999/check-user-conflicts",
        json={"usernames": ["m1"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_check_user_conflicts_requires_auth(client: AsyncClient):
    response = await client.post(
        "/v1/events/by_id/1/check-user-conflicts",
        json={"usernames": ["m1"]},
    )
    assert response.status_code == 401
