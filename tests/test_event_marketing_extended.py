"""Event Marketing module, the extended block (#410, marketing R5–R12).

Deployment-gated like credit and evaluations: every route registered,
503 while off. Holds the commercial detail one club wants and another
does not. Currency is stored but never exposed.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event_marketing import EventMarketing
from club_server.db.models.user import User, UserStatus
from club_server.services.auth import AuthService
from club_server.utils import generate_event_public_id, now_utc_ms

from .helpers import create_admin_user, create_coach_user, create_member_user

pytestmark = pytest.mark.usefixtures("event_marketing_enabled")

HOUR = 3_600_000


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _organizer(db: AsyncSession, username: str) -> str:
    db.add(
        User(
            username=username,
            password=AuthService.hash_password("coachpass123"),
            first_name=username.title(),
            status=UserStatus.active.value,
            is_super_admin=0,
            roles=json.dumps({"roles": ["coach"]}),
            created_at=now_utc_ms(),
        )
    )
    await db.flush()
    return AuthService.create_access_token(username, is_super_admin=False)


async def _venue(client: AsyncClient, admin: str) -> int:
    response = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=_auth(admin)
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _event(client: AsyncClient, admin: str, venue_id: int, **extra) -> dict:
    start = now_utc_ms() + 48 * HOUR
    body = {
        "title": "Camp",
        "type": "camp",
        "venueId": venue_id,
        "visibility": "public",
        "startTimeUtc": start,
        "endTimeUtc": start + HOUR,
        **extra,
    }
    response = await client.post("/v1/events", json=body, headers=_auth(admin))
    assert response.status_code == 201, response.text
    return response.json()


FULL = {
    "durationText": "6 days",
    "scheduleText": "25th – 30th December",
    "eligibilityText": "Age 5+",
    "eligibilityNote": "No experience needed",
    "registrationDeadlineUtc": 1766494740000,
    "hasOpenSlots": True,
    "urgencyText": "Limited seats!",
    "contactNumber": "+919000000000",
    "fee": 6999,
    "feeStructure": [{"name": "Camp fee", "amount": 6999, "period": None}],
    "packageOffers": [
        {
            "name": "Standard",
            "price": 6999,
            "description": "Full camp",
            "features": ["Gear"],
        }
    ],
    "offers": [
        {
            "title": "Early bird",
            "description": "10% off",
            "validUntilUtc": 1766000000000,
        }
    ],
    "clubMembership": {
        "title": "Members",
        "description": "Save",
        "benefits": ["Priority"],
    },
    "facilities": [{"name": "Rink", "description": "Olympic size", "iconName": None}],
}


# ── gate ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R5")
async def test_should_refuse_every_marketing_route_when_module_off(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    from club_server.config import settings

    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id)
    pid = generate_event_public_id(event["id"])
    monkeypatch.setattr(settings, "event_marketing_enabled", False)

    calls = [
        client.get(f"/v1/events/by_id/{event['id']}/marketing", headers=_auth(admin)),
        client.put(
            f"/v1/events/by_id/{event['id']}/marketing", json=FULL, headers=_auth(admin)
        ),
        client.delete(
            f"/v1/events/by_id/{event['id']}/marketing", headers=_auth(admin)
        ),
        client.get(f"/v1/public/events/{pid}/marketing"),
        client.get("/v1/public/events/marketing", params={"ids": pid}),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "EVENT_MARKETING_DISABLED"

    caps = await client.get("/v1/capabilities", headers=_auth(admin))
    assert caps.json()["eventMarketing"] is False
    monkeypatch.setattr(settings, "event_marketing_enabled", True)
    caps = await client.get("/v1/capabilities", headers=_auth(admin))
    assert caps.json()["eventMarketing"] is True


# ── ownership and round trip ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R6")
async def test_should_put_read_and_delete_marketing_as_admin_with_audit(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id)
    url = f"/v1/events/by_id/{event['id']}/marketing"

    assert (await client.get(url, headers=_auth(admin))).status_code == 404

    response = await client.put(url, json=FULL, headers=_auth(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eventId"] == event["id"]
    for key, value in FULL.items():
        assert body[key] == value, key
    assert "currency" not in body

    stored = (await db_session.execute(select(EventMarketing))).scalar_one()
    assert stored.currency == "INR"

    response = await client.put(url, json={"fee": 5000}, headers=_auth(admin))
    assert response.status_code == 200
    assert response.json()["fee"] == 5000
    assert response.json()["feeStructure"] is None  # PUT replaces the whole row

    fetched = await client.get(url, headers=_auth(admin))
    assert fetched.json()["fee"] == 5000

    deleted = await client.delete(url, headers=_auth(admin))
    assert deleted.status_code == 204
    assert (await client.get(url, headers=_auth(admin))).status_code == 404

    audit = await client.get(
        "/v1/audit_log",
        params={"action": "update_event_marketing"},
        headers=_auth(admin),
    )
    assert audit.json()["total"] == 2
    audit = await client.get(
        "/v1/audit_log",
        params={"action": "delete_event_marketing"},
        headers=_auth(admin),
    )
    assert audit.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R7")
async def test_should_let_organizer_write_and_coach_read_but_not_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    organizer = await _organizer(db_session, "orga")
    coach = await create_coach_user(db_session, "other_coach")
    member = await create_member_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id, organizerName="orga")
    url = f"/v1/events/by_id/{event['id']}/marketing"

    response = await client.put(url, json={"fee": 100}, headers=_auth(organizer))
    assert response.status_code == 200, response.text
    assert (await client.get(url, headers=_auth(coach))).status_code == 200
    assert (
        await client.put(url, json={"fee": 1}, headers=_auth(coach))
    ).status_code == 403
    assert (await client.delete(url, headers=_auth(coach))).status_code == 403
    assert (await client.get(url, headers=_auth(member))).status_code == 403
    assert (
        await client.put(url, json={"fee": 1}, headers=_auth(member))
    ).status_code == 403
    assert (await client.get(url)).status_code == 401


# ── validation and precedence ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R8")
async def test_should_reject_malformed_extended_values(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id)
    url = f"/v1/events/by_id/{event['id']}/marketing"

    for bad in (
        {"fee": -1},
        {"feeStructure": [{"name": "x"}]},
        {"feeStructure": "not a list"},
        {"packageOffers": [{"name": "x", "price": "free"}]},
        {"offers": [{"description": "no title"}]},
        {"clubMembership": {"description": "no title"}},
        {"facilities": [{"description": "no name"}]},
        {"currency": "USD"},
        {"unknownField": 1},
    ):
        response = await client.put(url, json=bad, headers=_auth(admin))
        assert response.status_code == 422, bad
    assert (await client.get(url, headers=_auth(admin))).status_code == 404

    response = await client.put(url, json={}, headers=_auth(admin))
    assert response.status_code == 200
    assert response.json()["fee"] is None
    assert response.json()["hasOpenSlots"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R9")
async def test_should_store_both_fee_and_fee_structure_without_reconciling(
    client: AsyncClient, db_session: AsyncSession
):
    """Precedence is the app's to apply: the server stores both as sent."""
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id)
    url = f"/v1/events/by_id/{event['id']}/marketing"

    response = await client.put(
        url,
        json={
            "fee": 1000,
            "feeStructure": [{"name": "Monthly", "amount": 1200, "period": "month"}],
        },
        headers=_auth(admin),
    )
    assert response.status_code == 200
    assert response.json()["fee"] == 1000
    assert response.json()["feeStructure"][0]["amount"] == 1200


# ── public reads ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R10")
async def test_should_read_public_event_marketing_by_public_id(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    public_event = await _event(client, admin, venue_id)
    private_event = await _event(client, admin, venue_id, visibility="private")
    bare_event = await _event(client, admin, venue_id)
    for e in (public_event, private_event):
        await client.put(
            f"/v1/events/by_id/{e['id']}/marketing", json=FULL, headers=_auth(admin)
        )

    response = await client.get(
        f"/v1/public/events/{generate_event_public_id(public_event['id'])}/marketing"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["publicId"] == generate_event_public_id(public_event["id"])
    assert body["fee"] == 6999
    assert body["contactNumber"] == "+919000000000"
    assert "currency" not in body and "eventId" not in body

    for pid in (
        generate_event_public_id(private_event["id"]),
        generate_event_public_id(bare_event["id"]),
        str(public_event["id"]),
        "nope",
    ):
        response = await client.get(f"/v1/public/events/{pid}/marketing")
        assert response.status_code == 404, pid


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R11")
async def test_should_batch_read_public_marketing_for_listing_cards(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    a = await _event(client, admin, venue_id, title="A")
    b = await _event(client, admin, venue_id, title="B")
    c = await _event(client, admin, venue_id, title="C", visibility="private")
    for e, fee in ((a, 100), (b, 200), (c, 300)):
        await client.put(
            f"/v1/events/by_id/{e['id']}/marketing",
            json={"fee": fee},
            headers=_auth(admin),
        )
    pids = [generate_event_public_id(e["id"]) for e in (a, b, c)]

    response = await client.get(
        "/v1/public/events/marketing", params={"ids": ",".join(pids + ["nope"])}
    )
    assert response.status_code == 200
    items = response.json()
    assert {i["publicId"]: i["fee"] for i in items} == {pids[0]: 100, pids[1]: 200}

    too_many = await client.get(
        "/v1/public/events/marketing", params={"ids": ",".join(["x"] * 51)}
    )
    assert too_many.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("marketing:R12")
async def test_should_drop_marketing_row_with_the_event(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id)
    await client.put(
        f"/v1/events/by_id/{event['id']}/marketing",
        json={"fee": 1},
        headers=_auth(admin),
    )
    soft = await client.delete(f"/v1/events/by_id/{event['id']}", headers=_auth(admin))
    assert soft.status_code in (200, 204)
    assert (
        await client.get(
            f"/v1/public/events/{generate_event_public_id(event['id'])}/marketing"
        )
    ).status_code == 404
    hard = await client.delete(
        f"/v1/events/by_id/{event['id']}/hard", headers=_auth(admin)
    )
    assert hard.status_code == 204, hard.text
    assert (await db_session.execute(select(EventMarketing))).scalars().all() == []
