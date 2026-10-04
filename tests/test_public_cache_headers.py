"""Cache headers on the public surface (#297, public R20–R22)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.requirement("public:R20")
async def test_should_cache_public_reads_with_a_content_etag(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    created = await client.post(
        "/v1/venues", json={"name": "Rink"}, headers=_auth(admin)
    )
    assert created.status_code == 201

    for path in (
        "/v1/public/venues",
        "/v1/public/staff",
        "/v1/public/events",
        "/v1/public/club-info",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "public, max-age=300", path
        assert response.headers["etag"].startswith('"'), path
        assert response.json() is not None

    first = (await client.get("/v1/public/venues")).headers["etag"]
    # Same content, same tag; the site's cache-buster query string changes nothing.
    assert (await client.get("/v1/public/venues?v=20260905")).headers["etag"] == first
    # Different content, different tag.
    await client.post("/v1/venues", json={"name": "Arena"}, headers=_auth(admin))
    assert (await client.get("/v1/public/venues")).headers["etag"] != first


@pytest.mark.asyncio
@pytest.mark.requirement("public:R21")
async def test_should_answer_304_on_matching_if_none_match(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    await client.post("/v1/venues", json={"name": "Rink"}, headers=_auth(admin))
    etag = (await client.get("/v1/public/venues")).headers["etag"]

    response = await client.get("/v1/public/venues", headers={"If-None-Match": etag})
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "public, max-age=300"

    response = await client.get(
        "/v1/public/venues", headers={"If-None-Match": '"stale"'}
    )
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Rink"


@pytest.mark.asyncio
@pytest.mark.requirement("public:R22")
async def test_should_not_cache_health_errors_or_member_reads(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    health = await client.get("/health")
    assert health.status_code == 200
    assert health.headers["cache-control"] == "no-store"
    assert "etag" not in health.headers

    missing = await client.get("/v1/public/venues/nope")
    assert missing.status_code == 404
    assert "etag" not in missing.headers and "cache-control" not in missing.headers

    member = await client.get("/v1/venues", headers=_auth(admin))
    assert member.status_code == 200
    assert "etag" not in member.headers and "cache-control" not in member.headers
