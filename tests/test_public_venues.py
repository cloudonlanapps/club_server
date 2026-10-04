"""Public venue surface and the member reads behind login (#307, venue R8–R9, R22–R25).

The website reads venues only through ``/v1/public/venues``; the member
endpoints under ``/v1/venues`` require a login again. Public venues are
addressed by an opaque public id, never the integer id.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.media_links import VenueMediaLink
from club_server.utils import generate_venue_public_id, now_utc_ms

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_media_row,
    create_member_user,
)

VENUE_IMAGE_TAG = "venue_image"


async def _seed(client: AsyncClient, db_session: AsyncSession) -> tuple[str, list[int]]:
    """Two live venues (the first is default) and one soft-deleted; returns admin token and ids."""
    admin = await create_admin_user(db_session)
    headers = {"Authorization": f"Bearer {admin}"}
    ids: list[int] = []
    for name, body in (
        (
            "Main Rink",
            {
                "address": "4th floor, the mall",
                "description": "**The** rink",
                "mapUri": "https://maps.example/embed|https://maps.example/share",
                "isDefault": True,
                "isFeatured": True,
            },
        ),
        ("Practice Rink", {}),
        ("Old Rink", {}),
    ):
        response = await client.post(
            "/v1/venues", json={"name": name, **body}, headers=headers
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    deleted = await client.delete(f"/v1/venues/by_id/{ids[2]}", headers=headers)
    assert deleted.status_code == 200
    return admin, ids


async def _link_image(
    db_session: AsyncSession, venue_id: int, media_uuid: str, *, created_at: int
) -> None:
    db_session.add(
        VenueMediaLink(
            venue_id=venue_id,
            media_uuid=media_uuid,
            tag=VENUE_IMAGE_TAG,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await db_session.flush()
    await db_session.commit()


# ── member reads require login ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R9")
async def test_should_refuse_venue_listing_when_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    await _seed(client, db_session)
    await db_session.commit()

    response = await client.get("/v1/venues")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R8")
async def test_should_refuse_venue_read_when_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    _, ids = await _seed(client, db_session)
    await db_session.commit()

    response = await client.get(f"/v1/venues/by_id/{ids[0]}")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R9")
async def test_should_list_venues_when_authenticated_any_role(
    client: AsyncClient, db_session: AsyncSession
):
    admin, ids = await _seed(client, db_session)
    coach = await create_coach_user(db_session)
    member = await create_member_user(db_session)
    await db_session.commit()

    for token in (admin, coach, member):
        response = await client.get(
            "/v1/venues", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert sorted(v["id"] for v in response.json()["items"]) == sorted(ids[:2])


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R8")
async def test_should_read_venue_when_authenticated_any_role(
    client: AsyncClient, db_session: AsyncSession
):
    admin, ids = await _seed(client, db_session)
    member = await create_member_user(db_session)
    await db_session.commit()

    for token in (admin, member):
        response = await client.get(
            f"/v1/venues/by_id/{ids[0]}", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Main Rink"


# ── public surface ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R22")
async def test_should_list_public_venues_when_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    _, ids = await _seed(client, db_session)
    await db_session.commit()

    response = await client.get("/v1/public/venues")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert [v["name"] for v in items] == ["Main Rink", "Practice Rink"]
    assert items[0]["publicId"] == generate_venue_public_id(ids[0])
    assert "id" not in items[0]
    assert "deletedAtUtc" not in items[0]
    assert items[0]["mapUri"] == "https://maps.example/embed|https://maps.example/share"
    assert items[0]["primaryVenueBadge"] == "Primary Training Venue"
    assert items[0]["isDefault"] is True
    assert items[0]["isFeatured"] is True
    assert items[1]["primaryVenueBadge"] is None
    assert items[0]["image"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R23")
async def test_should_read_public_venue_by_public_id(
    client: AsyncClient, db_session: AsyncSession
):
    _, ids = await _seed(client, db_session)
    await db_session.commit()

    response = await client.get(f"/v1/public/venues/{generate_venue_public_id(ids[1])}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Practice Rink"
    assert body["publicId"] == generate_venue_public_id(ids[1])
    assert "id" not in body


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R23")
async def test_should_return_404_for_unknown_or_integer_public_id(
    client: AsyncClient, db_session: AsyncSession
):
    _, ids = await _seed(client, db_session)
    await db_session.commit()

    for bad in ("nope", str(ids[0])):
        response = await client.get(f"/v1/public/venues/{bad}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "VENUE_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R24")
async def test_should_omit_deleted_venue_from_public_surface(
    client: AsyncClient, db_session: AsyncSession
):
    _, ids = await _seed(client, db_session)
    await db_session.commit()

    listed = (await client.get("/v1/public/venues")).json()
    assert generate_venue_public_id(ids[2]) not in {v["publicId"] for v in listed}

    response = await client.get(f"/v1/public/venues/{generate_venue_public_id(ids[2])}")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R25")
async def test_should_expose_newest_public_venue_image(
    client: AsyncClient, db_session: AsyncSession
):
    _, ids = await _seed(client, db_session)
    older = await create_media_row(db_session, uploaded_by="admin", public=True)
    newer = await create_media_row(db_session, uploaded_by="admin", public=True)
    now = now_utc_ms()
    await _link_image(db_session, ids[0], older, created_at=now - 1000)
    await _link_image(db_session, ids[0], newer, created_at=now)

    response = await client.get(f"/v1/public/venues/{generate_venue_public_id(ids[0])}")
    assert response.status_code == 200
    assert response.json()["image"]["uuid"] == newer

    listed = (await client.get("/v1/public/venues")).json()
    assert listed[0]["image"]["uuid"] == newer
    assert listed[1]["image"] is None


@pytest.mark.asyncio
@pytest.mark.requirement("venue:R25")
async def test_should_hide_venue_image_when_newest_is_private(
    client: AsyncClient, db_session: AsyncSession
):
    """A private current image yields null; an older public one is never resurfaced."""
    _, ids = await _seed(client, db_session)
    public_old = await create_media_row(db_session, uploaded_by="admin", public=True)
    private_new = await create_media_row(db_session, uploaded_by="admin", public=False)
    now = now_utc_ms()
    await _link_image(db_session, ids[0], public_old, created_at=now - 1000)
    await _link_image(db_session, ids[0], private_new, created_at=now)

    response = await client.get(f"/v1/public/venues/{generate_venue_public_id(ids[0])}")
    assert response.status_code == 200
    assert response.json()["image"] is None
