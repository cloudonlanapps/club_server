"""Tests for #100: DOB-shaped ms fields must be at UTC midnight.

The server treats ``users.date_of_birth`` and the
``dob_on_or_after_utc`` / ``dob_on_or_before_utc`` fields on groups and
events as calendar dates encoded as UTC-midnight ms-since-epoch. Any
non-midnight value indicates a client-side timezone bug (e.g. a picker
emitting local-midnight) and is rejected with 422 rather than silently
flattened to an unintended calendar date.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user


MS_PER_DAY = 86_400_000
DOB_2014_UTC_MIDNIGHT = 1388534400000  # 2014-01-01 00:00:00 UTC
DOB_2010_UTC_MIDNIGHT = 1262304000000  # 2010-01-01 00:00:00 UTC
IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000
# Local-midnight emitted by an IST picker for 2014-01-01.
DOB_2014_IST_LOCAL_MIDNIGHT = DOB_2014_UTC_MIDNIGHT - IST_OFFSET_MS


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# -----------------------------------------------------------------------------
# users.date_of_birth
# -----------------------------------------------------------------------------


@pytest.mark.requirement("users:R9")
@pytest.mark.asyncio
async def test_admin_create_user_with_non_midnight_dob_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/users",
        json={
            "username": "istpicker",
            "passwordHash": "h",
            "firstName": "Ist",
            "gender": "male",
            "dateOfBirthUtc": DOB_2014_IST_LOCAL_MIDNIGHT,
            "phone": "1234567890",
        },
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dateOfBirthUtc"


@pytest.mark.requirement("users:R9")
@pytest.mark.asyncio
async def test_admin_create_user_with_midnight_dob_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/users",
        json={
            "username": "okuser",
            "passwordHash": "h",
            "firstName": "Ok",
            "gender": "male",
            "dateOfBirthUtc": DOB_2014_UTC_MIDNIGHT,
            "phone": "1234567890",
        },
        headers=auth(token),
    )
    assert response.status_code == 201
    assert response.json()["dateOfBirthUtc"] == DOB_2014_UTC_MIDNIGHT

    verify = await client.get("/v1/users/by_id/okuser/private", headers=auth(token))
    assert verify.status_code == 200
    assert verify.json()["dateOfBirthUtc"] == DOB_2014_UTC_MIDNIGHT


@pytest.mark.requirement("users:R9")
@pytest.mark.asyncio
async def test_admin_patch_user_with_non_midnight_dob_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    create = await client.post(
        "/v1/users",
        json={
            "username": "patchme",
            "passwordHash": "h",
            "firstName": "Pat",
            "gender": "male",
            "dateOfBirthUtc": DOB_2014_UTC_MIDNIGHT,
            "phone": "1234567890",
        },
        headers=auth(token),
    )
    assert create.status_code == 201

    response = await client.patch(
        "/v1/users/by_id/patchme",
        json={"dateOfBirthUtc": DOB_2014_IST_LOCAL_MIDNIGHT},
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"

    # Stored value must be unchanged.
    verify = await client.get("/v1/users/by_id/patchme/private", headers=auth(token))
    assert verify.status_code == 200
    assert verify.json()["dateOfBirthUtc"] == DOB_2014_UTC_MIDNIGHT


@pytest.mark.requirement("users:R9")
@pytest.mark.asyncio
async def test_register_with_non_midnight_dob_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "regbad",
            "email": "regbad@example.com",
            "password": "testpass123",
            "firstName": "Reg",
            "gender": "male",
            "dateOfBirthUtc": DOB_2014_IST_LOCAL_MIDNIGHT,
            "phone": "1234567890",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"


@pytest.mark.requirement("users:R9")
@pytest.mark.asyncio
async def test_register_with_midnight_dob_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.post(
        "/v1/auth/register",
        json={
            "username": "regok",
            "email": "regok@example.com",
            "password": "testpass123",
            "firstName": "Reg",
            "gender": "male",
            "dateOfBirthUtc": DOB_2014_UTC_MIDNIGHT,
            "phone": "1234567890",
        },
    )
    assert response.status_code in (200, 201)
    assert response.json()["dateOfBirthUtc"] == DOB_2014_UTC_MIDNIGHT


# -----------------------------------------------------------------------------
# groups.dob_on_or_after_utc / dob_on_or_before_utc
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_group_with_non_midnight_lower_bound_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups",
        json={
            "name": "G",
            "dobOnOrAfterUtc": DOB_2010_UTC_MIDNIGHT + 1,
            "dobOnOrBeforeUtc": DOB_2014_UTC_MIDNIGHT,
        },
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dobOnOrAfterUtc"


@pytest.mark.asyncio
async def test_create_group_with_non_midnight_upper_bound_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups",
        json={
            "name": "G",
            "dobOnOrAfterUtc": DOB_2010_UTC_MIDNIGHT,
            "dobOnOrBeforeUtc": DOB_2014_UTC_MIDNIGHT - IST_OFFSET_MS,
        },
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dobOnOrBeforeUtc"


@pytest.mark.asyncio
async def test_create_group_with_midnight_bounds_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/groups",
        json={
            "name": "G",
            "dobOnOrAfterUtc": DOB_2010_UTC_MIDNIGHT,
            "dobOnOrBeforeUtc": DOB_2014_UTC_MIDNIGHT,
        },
        headers=auth(token),
    )
    assert response.status_code in (200, 201)
    body = response.json()
    assert body["dobOnOrAfterUtc"] == DOB_2010_UTC_MIDNIGHT
    assert body["dobOnOrBeforeUtc"] == DOB_2014_UTC_MIDNIGHT


@pytest.mark.asyncio
async def test_patch_group_with_non_midnight_bound_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    create = await client.post(
        "/v1/groups",
        json={
            "name": "G",
            "dobOnOrAfterUtc": DOB_2010_UTC_MIDNIGHT,
            "dobOnOrBeforeUtc": DOB_2014_UTC_MIDNIGHT,
        },
        headers=auth(token),
    )
    assert create.status_code in (200, 201)
    gid = create.json()["id"]

    response = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": DOB_2010_UTC_MIDNIGHT + 12 * 60 * 60 * 1000},
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"


# -----------------------------------------------------------------------------
# events.dob_on_or_after_utc / dob_on_or_before_utc
# -----------------------------------------------------------------------------


async def _create_venue(client: AsyncClient, token: str) -> int:
    v = await client.post(
        "/v1/venues",
        json={"name": "V1"},
        headers=auth(token),
    )
    assert v.status_code in (200, 201)
    return v.json()["id"]


_SOON = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)


def _event_payload(venue_id: int, **overrides: object) -> dict:
    base: dict = {
        "title": "E",
        "type": "oneOff",
        "visibility": "public",
        "venueId": venue_id,
        "startTimeUtc": _SOON,
        "endTimeUtc": _SOON + 3600000,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_event_with_non_midnight_lower_bound_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    vid = await _create_venue(client, token)
    response = await client.post(
        "/v1/events",
        json=_event_payload(
            vid,
            dobOnOrAfterUtc=DOB_2010_UTC_MIDNIGHT + 1,
            dobOnOrBeforeUtc=DOB_2014_UTC_MIDNIGHT,
        ),
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dobOnOrAfterUtc"


@pytest.mark.asyncio
async def test_create_event_with_non_midnight_upper_bound_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    vid = await _create_venue(client, token)
    response = await client.post(
        "/v1/events",
        json=_event_payload(
            vid,
            dobOnOrAfterUtc=DOB_2010_UTC_MIDNIGHT,
            dobOnOrBeforeUtc=DOB_2014_UTC_MIDNIGHT - IST_OFFSET_MS,
        ),
        headers=auth(token),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
    assert detail["field"] == "dobOnOrBeforeUtc"


@pytest.mark.asyncio
async def test_create_event_with_midnight_bounds_succeeds(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    vid = await _create_venue(client, token)
    response = await client.post(
        "/v1/events",
        json=_event_payload(
            vid,
            dobOnOrAfterUtc=DOB_2010_UTC_MIDNIGHT,
            dobOnOrBeforeUtc=DOB_2014_UTC_MIDNIGHT,
        ),
        headers=auth(token),
    )
    assert response.status_code in (200, 201)
    body = response.json()
    assert body["dobOnOrAfterUtc"] == DOB_2010_UTC_MIDNIGHT
    assert body["dobOnOrBeforeUtc"] == DOB_2014_UTC_MIDNIGHT


@pytest.mark.asyncio
async def test_patch_event_with_non_midnight_bound_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    vid = await _create_venue(client, token)
    create = await client.post(
        "/v1/events",
        json=_event_payload(
            vid,
            dobOnOrAfterUtc=DOB_2010_UTC_MIDNIGHT,
            dobOnOrBeforeUtc=DOB_2014_UTC_MIDNIGHT,
        ),
        headers=auth(token),
    )
    assert create.status_code in (200, 201)
    eid = create.json()["id"]

    response = await client.patch(
        f"/v1/events/by_id/{eid}",
        json={"dobOnOrBeforeUtc": DOB_2014_UTC_MIDNIGHT + 1, "version": 1},
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_DOB_NOT_UTC_MIDNIGHT"
