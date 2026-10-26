"""The download access check runs before anything that describes the file (#80).

A caller the file does not admit gets 401 or 403 whatever state the file is in:
neither its conversion state nor its media type (named by an invalid-variant
error) leaks to someone holding only its uuid. A caller it admits still gets
the 409 / 422 / 400 those states produce.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.media import Media

from .helpers import create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir as clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("clean_upload_dir")


async def _private_media(
    client: AsyncClient, db_session: AsyncSession, conversion_status: str
) -> tuple[str, str, str]:
    """Alice's self-only image in the given conversion state; returns
    (uuid, alice's token, bob's token)."""
    alice = await create_member_user(db_session, "alice")
    bob = await create_member_user(db_session, "bob")
    media = await upload(client, alice, access_roles=["self"])
    _ = await db_session.execute(
        update(Media)
        .where(Media.uuid == media["uuid"])
        .values(conversion_status=conversion_status)
    )
    await db_session.commit()
    return media["uuid"], alice, bob


@pytest.mark.asyncio
@pytest.mark.requirement("media:R30")
async def test_should_require_login_when_private_media_is_still_converting(
    client: AsyncClient, db_session: AsyncSession
):
    uuid, _alice, _bob = await _private_media(client, db_session, "processing")

    response = await client.get(f"/v1/media/by_id/{uuid}/download")

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R31")
async def test_should_forbid_caller_without_access_when_conversion_failed(
    client: AsyncClient, db_session: AsyncSession
):
    uuid, _alice, bob = await _private_media(client, db_session, "failed")

    response = await client.get(f"/v1/media/by_id/{uuid}/download", headers=auth(bob))

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R31")
async def test_should_forbid_caller_without_access_when_variant_is_invalid(
    client: AsyncClient, db_session: AsyncSession
):
    uuid, _alice, bob = await _private_media(client, db_session, "completed")

    response = await client.get(
        f"/v1/media/by_id/{uuid}/download",
        params={"variant": "poster"},
        headers=auth(bob),
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "FORBIDDEN"
    assert "image" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversion_status", "variant", "expected_status", "expected_code"),
    [
        ("processing", "original", 409, "CONVERSION_IN_PROGRESS"),
        ("failed", "original", 422, "CONVERSION_FAILED"),
        ("completed", "poster", 400, "INVALID_VARIANT"),
    ],
)
async def test_should_report_file_state_to_a_caller_with_access(
    client: AsyncClient,
    db_session: AsyncSession,
    conversion_status: str,
    variant: str,
    expected_status: int,
    expected_code: str,
):
    uuid, alice, _bob = await _private_media(client, db_session, conversion_status)

    response = await client.get(
        f"/v1/media/by_id/{uuid}/download",
        params={"variant": variant},
        headers=auth(alice),
    )

    assert response.status_code == expected_status, response.text
    assert response.json()["detail"]["code"] == expected_code
