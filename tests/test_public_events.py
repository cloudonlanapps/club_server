"""Public event catalogue (#299, public R8–R15).

The website lists events by type in a time window and shows one page per
event. Events are addressed by an opaque public id, coaches are embedded
as public profiles (never usernames), the venue as its public projection,
media as public uuids, and the basic marketing block rides along.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.event_schedule import EventSchedule
from club_server.db.models.media_links import EventMediaLink
from club_server.db.models.user import User, UserStatus
from club_server.services.auth import AuthService
from club_server.utils import (
    generate_event_public_id,
    generate_public_id,
    generate_venue_public_id,
    now_utc_ms,
)

from .eligibility_helpers import with_event_band
from .helpers import create_admin_user, create_media_row

HOUR = 3_600_000


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _coach(db: AsyncSession, username: str, *, opted_in: bool) -> None:
    db.add(
        User(
            username=username,
            password=AuthService.hash_password("coachpass123"),
            first_name=username.title(),
            use_name_publicly=1,
            is_public_profile=1 if opted_in else 0,
            status=UserStatus.active.value,
            is_super_admin=0,
            roles=json.dumps({"roles": ["coach"]}),
            created_at=now_utc_ms(),
        )
    )
    await db.flush()


async def _venue(client: AsyncClient, admin: str, name: str = "Rink") -> int:
    response = await client.post(
        "/v1/venues", json={"name": name}, headers=_auth(admin)
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _event(
    client: AsyncClient,
    admin: str,
    venue_id: int,
    *,
    title: str = "Camp",
    event_type: str = "camp",
    visibility: str = "public",
    start_in_hours: int = 48,
    coach_names: list[str] | None = None,
    **extra,
) -> dict:
    start = now_utc_ms() + start_in_hours * HOUR
    body = {
        "title": title,
        "type": event_type,
        "venueId": venue_id,
        "visibility": visibility,
        "startTimeUtc": start,
        "endTimeUtc": start + HOUR,
        **({"rrule": "FREQ=WEEKLY;BYDAY=SA"} if event_type == "programme" else {}),
        **({"coachNames": coach_names} if coach_names else {}),
        **extra,
    }
    response = await client.post(
        "/v1/events", json=with_event_band(body), headers=_auth(admin)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _move_to_past(db: AsyncSession, event_id: int) -> None:
    """Shift the event's only schedule a week back so every occurrence has ended."""
    now = now_utc_ms()
    await db.execute(
        update(EventSchedule)
        .where(EventSchedule.event_id == event_id)
        .values(start_time=now - 7 * 24 * HOUR, end_time=now - 7 * 24 * HOUR + HOUR)
    )
    await db.commit()


async def _link(
    db: AsyncSession, event_id: int, media_uuid: str, tag: str, at: int
) -> None:
    db.add(
        EventMediaLink(
            event_id=event_id,
            media_uuid=media_uuid,
            tag=tag,
            created_at=at,
            updated_at=at,
        )
    )
    await db.flush()
    await db.commit()


def _pid(event: dict) -> str:
    return generate_event_public_id(event["id"])


# ── listing ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R8")
async def test_should_list_public_live_events_without_ids_or_usernames(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _coach(db_session, "suraj", opted_in=True)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    shown = await _event(client, admin, venue_id, coach_names=["suraj"])
    await _event(client, admin, venue_id, title="Private", visibility="private")
    gone = await _event(client, admin, venue_id, title="Deleted")
    deleted = await client.delete(
        f"/v1/events/by_id/{gone['id']}", headers=_auth(admin)
    )
    assert deleted.status_code in (200, 204)

    response = await client.get("/v1/public/events")
    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"items", "total", "offset", "limit"}
    assert page["total"] == 1
    item = page["items"][0]
    assert item["publicId"] == _pid(shown)
    assert item["title"] == "Camp"
    assert item["venueId"] == generate_venue_public_id(venue_id)
    for forbidden in ("id", "organizerName", "coachNames", "galleryUris", "venue_id"):
        assert forbidden not in item, forbidden
    assert "suraj" not in response.text
    assert "admin" not in response.text


@pytest.mark.asyncio
@pytest.mark.requirement("public:R9")
async def test_should_filter_by_type_featured_venue_and_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    rink = await _venue(client, admin, "Rink")
    arena = await _venue(client, admin, "Arena")
    camp = await _event(
        client, admin, rink, title="Camp", event_type="camp", isFeatured=True
    )
    prog = await _event(client, admin, rink, title="Prog", event_type="programme")
    oneoff = await _event(client, admin, arena, title="Once", event_type="oneOff")
    past = await _event(client, admin, rink, title="Past", event_type="oneOff")
    await _move_to_past(db_session, past["id"])

    async def titles(**params) -> list[str]:
        response = await client.get("/v1/public/events", params=params)
        assert response.status_code == 200, response.text
        return [i["title"] for i in response.json()["items"]]

    assert await titles(type="camp") == ["Camp"]
    assert await titles(type="programme") == ["Prog"]
    assert await titles(featured="true") == ["Camp"]
    assert await titles(venueId=generate_venue_public_id(arena)) == ["Once"]
    now = now_utc_ms()
    upcoming = await titles(**{"from": now})
    assert "Past" not in upcoming and {"Camp", "Prog", "Once"} <= set(upcoming)
    assert await titles(to=now) == ["Past"]
    assert await titles(**{"from": now - 30 * 24 * HOUR, "to": now}) == ["Past"]
    assert (
        await client.get("/v1/public/events", params={"type": "gala"})
    ).status_code == 422
    paged = await client.get("/v1/public/events", params={"limit": 1, "offset": 1})
    assert paged.json()["total"] == 4 and len(paged.json()["items"]) == 1
    assert camp["id"] != prog["id"] != oneoff["id"]


# ── detail ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R10")
async def test_should_read_public_event_by_public_id_and_404_otherwise(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(
        client,
        admin,
        venue_id,
        sessions=[
            {"name": "Warm-up", "periodMinutes": 15},
            {"name": "Ice", "periodMinutes": 45},
        ],
        gender="female",
        dobOnOrAfterUtc=946684800000,
    )
    private = await _event(client, admin, venue_id, visibility="private")

    response = await client.get(f"/v1/public/events/{_pid(event)}")
    assert response.status_code == 200
    body = response.json()
    assert body["publicId"] == _pid(event)
    assert body["type"] == "camp"
    assert body["sessions"] == [
        {"name": "Warm-up", "periodMinutes": 15},
        {"name": "Ice", "periodMinutes": 45},
    ]
    assert body["gender"] == "female"
    assert body["dobOnOrAfterUtc"] == 946684800000
    assert body["isPast"] is False
    assert "id" not in body and "coachNames" not in body

    for bad in (_pid(private), str(event["id"]), "nope"):
        response = await client.get(f"/v1/public/events/{bad}")
        assert response.status_code == 404, bad
        assert response.json()["detail"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("public:R11")
async def test_should_embed_consenting_coaches_and_guests_as_public_profiles(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _coach(db_session, "suraj", opted_in=True)
    await _coach(db_session, "shy", opted_in=False)
    await db_session.commit()
    guest = await client.post(
        "/v1/users",
        json={
            "username": "guest_amit",
            "passwordHash": AuthService.hash_password("x" * 12),
            "firstName": "Amit",
            "lastName": "Belwal",
            "gender": "male",
            "dateOfBirthUtc": 315532800000,
            "phone": "+910000000000",
            "useNamePublicly": True,
            "isGuest": True,
        },
        headers=_auth(admin),
    )
    assert guest.status_code == 201, guest.text
    venue_id = await _venue(client, admin)
    event = await _event(
        client, admin, venue_id, coach_names=["shy", "suraj", "guest_amit"]
    )

    response = await client.get(f"/v1/public/events/{_pid(event)}")
    assert response.status_code == 200
    coaches = response.json()["coaches"]
    assert [c["displayName"] for c in coaches] == ["Suraj", "Amit Belwal"]
    assert [c["isGuest"] for c in coaches] == [False, True]
    assert coaches[0]["publicId"] == generate_public_id("suraj")
    assert "shy" not in response.text
    assert "username" not in response.text


@pytest.mark.asyncio
@pytest.mark.requirement("public:R12")
async def test_should_embed_venue_as_public_projection(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin, "Main Rink")
    event = await _event(client, admin, venue_id)

    response = await client.get(f"/v1/public/events/{_pid(event)}")
    venue = response.json()["venue"]
    assert venue["publicId"] == generate_venue_public_id(venue_id)
    assert venue["name"] == "Main Rink"
    assert "id" not in venue


@pytest.mark.asyncio
@pytest.mark.requirement("public:R13")
@pytest.mark.requirement("public:R31")
async def test_should_expose_public_cover_and_gallery_media_only(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id)
    now = now_utc_ms()
    old_cover = await create_media_row(db_session, uploaded_by="admin", public=True)
    new_cover = await create_media_row(db_session, uploaded_by="admin", public=True)
    pic1 = await create_media_row(db_session, uploaded_by="admin", public=True)
    secret = await create_media_row(db_session, uploaded_by="admin", public=False)
    pic2 = await create_media_row(db_session, uploaded_by="admin", public=True)
    await _link(db_session, event["id"], old_cover, "event_cover", now - 2000)
    await _link(db_session, event["id"], new_cover, "event_cover", now - 1000)
    await _link(db_session, event["id"], pic1, "event_gallery", now - 300)
    await _link(db_session, event["id"], secret, "event_gallery", now - 200)
    await _link(db_session, event["id"], pic2, "event_gallery", now - 100)

    response = await client.get(f"/v1/public/events/{_pid(event)}")
    body = response.json()
    assert body["cover"]["uuid"] == new_cover
    assert [m["uuid"] for m in body["gallery"]] == [pic1, pic2]
    listed = (await client.get("/v1/public/events")).json()["items"][0]
    assert listed["cover"]["uuid"] == new_cover
    assert [m["uuid"] for m in listed["gallery"]] == [pic1, pic2]

    # Each item says what it is and how to name it, so a client does not have
    # to fetch the file to find out (#424).
    cover = body["cover"]
    assert set(cover) == {"uuid", "mimeType", "filename"}
    assert cover["mimeType"].startswith("image/")
    assert cover["filename"].startswith(new_cover[:8])


@pytest.mark.asyncio
@pytest.mark.requirement("public:R14")
async def test_should_report_is_past_and_basic_marketing_block(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    plain = await _event(client, admin, venue_id, title="Plain")
    dressed = await _event(
        client,
        admin,
        venue_id,
        title="Dressed",
        shortDescription="Six days on ice",
        stamp="Christmas Camp!",
        highlights=["Skating"],
        includes=None,
    )
    await _move_to_past(db_session, plain["id"])

    body = (await client.get(f"/v1/public/events/{_pid(plain)}")).json()
    assert body["isPast"] is True
    assert body["marketing"] is None

    body = (await client.get(f"/v1/public/events/{_pid(dressed)}")).json()
    assert body["isPast"] is False
    assert body["marketing"] == {
        "shortDescription": "Six days on ice",
        "stamp": "Christmas Camp!",
        "highlights": ["Skating"],
        "includes": None,
    }


# ── authenticated reads gain the same coach projection ─────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R15")
async def test_should_fill_coaches_on_authenticated_event_reads(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _coach(db_session, "suraj", opted_in=True)
    await _coach(db_session, "shy", opted_in=False)
    await db_session.commit()
    venue_id = await _venue(client, admin)
    event = await _event(client, admin, venue_id, coach_names=["suraj", "shy"])

    single = await client.get(f"/v1/events/by_id/{event['id']}", headers=_auth(admin))
    assert single.status_code == 200
    assert single.json()["coachNames"] == ["suraj", "shy"]
    assert [c["publicId"] for c in single.json()["coaches"]] == [
        generate_public_id("suraj")
    ]

    listed = await client.get("/v1/events", headers=_auth(admin))
    item = next(e for e in listed.json()["items"] if e["id"] == event["id"])
    assert [c["displayName"] for c in item["coaches"]] == ["Suraj"]
    rows = (await db_session.execute(select(EventSchedule))).scalars().all()
    assert len(rows) == 1
