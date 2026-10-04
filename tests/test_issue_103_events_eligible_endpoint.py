"""Tests for issue #103: GET /events/by_id/{event_id}/eligible endpoint."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import attach_identity_document, create_admin_user

DOB_2010 = 1262304000000  # 2010-01-01 UTC midnight
DOB_2014 = 1388534400000  # 2014-01-01 UTC midnight
DOB_2000 = 946684800000  # 2000-01-01 UTC midnight


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def future_time(hours: int = 24) -> int:
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)


async def _create_venue(client: AsyncClient, admin_token: str) -> int:
    r = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=auth(admin_token)
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _register_member(
    client: AsyncClient,
    admin_token: str,
    username: str,
    db_session: AsyncSession,
    *,
    gender: str = "male",
    date_of_birth: int = DOB_2000,
) -> None:
    reg = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "testpass123",
            "firstName": username.capitalize(),
            "gender": gender,
            "dateOfBirthUtc": date_of_birth,
            "phone": "1234567890",
        },
    )
    assert reg.status_code in (200, 201)
    login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "testpass123"},
    )
    assert login.status_code == 200, login.text
    user_token = login.json()["accessToken"]
    await attach_identity_document(db_session, username)
    submit = await client.post(
        "/v1/users/me/submit-for-review",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert submit.status_code == 200, submit.text
    appr = await client.post(
        f"/v1/users/by_id/{username}/approve", headers=auth(admin_token)
    )
    assert appr.status_code in (200, 201, 204)


async def _create_event(
    client: AsyncClient,
    admin_token: str,
    venue_id: int,
    *,
    gender: str | None = None,
    dob_on_or_after_utc: int | None = None,
    dob_on_or_before_utc: int | None = None,
) -> dict:
    body: dict = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "visibility": "public",
        "startTimeUtc": future_time(24),
        "endTimeUtc": future_time(25),
    }
    if gender is not None:
        body["gender"] = gender
    if dob_on_or_after_utc is not None:
        body["dobOnOrAfterUtc"] = dob_on_or_after_utc
    if dob_on_or_before_utc is not None:
        body["dobOnOrBeforeUtc"] = dob_on_or_before_utc
    r = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert r.status_code == 201, r.text
    return r.json()


def _usernames(payload: list[dict]) -> set[str]:
    return {u["username"] for u in payload}


@pytest.mark.asyncio
async def test_issue_103_event_with_no_criteria_returns_all_active_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    await _register_member(client, admin_token, "alice", db_session)
    await _register_member(client, admin_token, "bob", db_session)
    event = await _create_event(client, admin_token, venue_id)

    r = await client.get(
        f"/v1/events/by_id/{event['id']}/eligible", headers=auth(admin_token)
    )
    assert r.status_code == 200
    names = _usernames(r.json())
    assert "alice" in names
    assert "bob" in names


@pytest.mark.asyncio
async def test_issue_103_gender_constraint_excludes_wrong_gender(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    await _register_member(client, admin_token, "male_user", db_session, gender="male")
    await _register_member(
        client, admin_token, "female_user", db_session, gender="female"
    )
    event = await _create_event(client, admin_token, venue_id, gender="female")

    r = await client.get(
        f"/v1/events/by_id/{event['id']}/eligible", headers=auth(admin_token)
    )
    assert r.status_code == 200
    names = _usernames(r.json())
    assert "female_user" in names
    assert "male_user" not in names


@pytest.mark.asyncio
async def test_issue_103_dob_window_excludes_out_of_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    await _register_member(
        client, admin_token, "young", db_session, date_of_birth=DOB_2014
    )
    await _register_member(
        client, admin_token, "old", db_session, date_of_birth=DOB_2000
    )
    event = await _create_event(
        client, admin_token, venue_id, dob_on_or_after_utc=DOB_2010
    )

    r = await client.get(
        f"/v1/events/by_id/{event['id']}/eligible", headers=auth(admin_token)
    )
    assert r.status_code == 200
    names = _usernames(r.json())
    assert "young" in names
    assert "old" not in names


@pytest.mark.asyncio
async def test_issue_103_excludes_already_enrolled_users(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    venue_id = await _create_venue(client, admin_token)
    await _register_member(client, admin_token, "enrolled_user", db_session)
    await _register_member(client, admin_token, "pending_user", db_session)
    event = await _create_event(client, admin_token, venue_id)

    invite = await client.post(
        f"/v1/events/by_id/{event['id']}/enrollments/invite",
        json={"membernames": ["enrolled_user"]},
        headers=auth(admin_token),
    )
    assert invite.status_code in (200, 201, 204)

    r = await client.get(
        f"/v1/events/by_id/{event['id']}/eligible", headers=auth(admin_token)
    )
    assert r.status_code == 200
    names = _usernames(r.json())
    assert "enrolled_user" not in names
    assert "pending_user" in names


@pytest.mark.asyncio
async def test_issue_103_unknown_event_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    r = await client.get(
        "/v1/events/by_id/99999999/eligible", headers=auth(admin_token)
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "EVENT_NOT_FOUND"
