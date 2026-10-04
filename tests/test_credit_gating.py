"""The credit system is optional per deployment (#294).

Covers R92-R98: the subsystem is off unless the deployment turns it on,
every endpoint still answers when it is off, the refusal is uniform, and
nothing is destroyed by switching it off and on again.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_member, days_from_now, open_account
from .helpers import create_admin_user, create_coach_user
from .test_attendance import create_event_and_enroll

DISABLED_PATHS = [
    ("GET", "/v1/credits/accounts"),
    ("GET", "/v1/credits/accounts/ABCD2345"),
    ("GET", "/v1/credits/entries"),
]


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R93")
async def test_should_refuse_credit_endpoints_when_system_disabled_by_default(
    client: AsyncClient, db_session: AsyncSession
):
    """R93: the default is off — a deployment that says nothing has no credit."""
    admin_token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/credits/accounts",
        json={
            "membername": "alice",
            "credits": 5,
            "validFromUtc": days_from_now(-1),
            "validUntilUtc": days_from_now(30),
            "reason": "Package purchased",
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CREDIT_SYSTEM_DISABLED"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), DISABLED_PATHS)
@pytest.mark.requirement("credit:R94")
async def test_should_still_answer_when_system_disabled(
    client: AsyncClient, db_session: AsyncSession, method: str, path: str
):
    """R94: the endpoints exist on every deployment; only the answer differs."""
    admin_token = await create_admin_user(db_session)

    response = await client.request(method, path, headers=auth(admin_token))

    assert response.status_code == 503, f"{method} {path}"
    assert response.json()["detail"]["code"] == "CREDIT_SYSTEM_DISABLED"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R94a")
async def test_should_refuse_before_checking_permission_when_system_disabled(
    client: AsyncClient, db_session: AsyncSession
):
    """R94a: the refusal happens at the edge, before any handler work.

    A coach would be rejected with 403 on an enabled deployment. Getting
    503 instead proves the gate ran first and no credit table was touched.
    """
    _ = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session)

    response = await client.get("/v1/credits/accounts", headers=auth(coach_token))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CREDIT_SYSTEM_DISABLED"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R94a")
async def test_should_refuse_anonymous_caller_when_system_disabled(
    client: AsyncClient,
):
    """R94a: the gate precedes authentication too — one uniform refusal."""
    response = await client.get("/v1/credits/accounts")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CREDIT_SYSTEM_DISABLED"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R92")
async def test_should_serve_credit_endpoints_when_system_enabled(
    client: AsyncClient, db_session: AsyncSession, credit_enabled: None
):
    """R92: the same endpoint works once the deployment turns it on."""
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")

    account = await open_account(client, admin_token, "alice", credits=5)

    assert account["balance"] == 5
    assert account["state"] == "usable"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R96")
async def test_should_keep_accounts_when_system_switched_off_and_on(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """R96: disabling destroys nothing; re-enabling resumes from the data."""
    from club_server.config import settings

    monkeypatch.setattr(settings, "credit_system_enabled", True)
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    account = await open_account(client, admin_token, "alice", credits=9)
    account_id = account["accountId"]

    monkeypatch.setattr(settings, "credit_system_enabled", False)
    while_off = await client.get(
        f"/v1/credits/accounts/{account_id}", headers=auth(admin_token)
    )
    assert while_off.status_code == 503

    monkeypatch.setattr(settings, "credit_system_enabled", True)
    back_on = await client.get(
        f"/v1/credits/accounts/{account_id}", headers=auth(admin_token)
    )

    assert back_on.status_code == 200
    assert back_on.json()["balance"] == 9
    assert back_on.json()["membername"] == "alice"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R98")
async def test_should_reject_credit_disposition_when_system_disabled(
    client: AsyncClient, db_session: AsyncSession
):
    """R98: a disposition on a non-credit deployment is refused, not ignored.

    Silently accepting it would tell the admin their penalty had been
    applied when no such thing had happened.
    """
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    venue = await client.post(
        "/v1/venues", json={"name": "V"}, headers=auth(admin_token)
    )
    start = days_from_now(1)
    event = await client.post(
        "/v1/events",
        json={
            "title": "P",
            "type": "programme",
            "venueId": venue.json()["id"],
            "startTimeUtc": start,
            "endTimeUtc": start + 3600000,
        },
        headers=auth(admin_token),
    )
    event_id = event.json()["id"]
    _ = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": ["alice"]},
        headers=auth(admin_token),
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={
            "membernames": ["alice"],
            "creditDisposition": {
                "penalty": 5,
                "validFromUtc": days_from_now(-1),
                "validUntilUtc": days_from_now(90),
                "reason": "Left",
            },
        },
        headers=auth(admin_token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CREDIT_DISPOSITION_NOT_APPLICABLE"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R97")
async def test_should_keep_bulk_mark_shape_when_system_disabled(
    client: AsyncClient, db_session: AsyncSession
):
    """R97: no response shape varies with the flag.

    A deployment without credits gets the same per-member report, with an
    always-empty ``refused`` list. Were the shape to depend on
    configuration, the SDK would face two contracts for one path.
    """
    admin_token = await create_admin_user(db_session)
    await create_member(db_session, "alice")
    event_id, occurrence = await create_event_and_enroll(
        client, admin_token, "alice", in_past=True, db_session=db_session
    )

    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": "alice", "status": "present"}]},
        headers=auth(admin_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refused"] == []
    assert [row["membername"] for row in body["marked"]] == ["alice"]
