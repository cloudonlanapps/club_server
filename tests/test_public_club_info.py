"""Club info and site media for the public website (#296, public R16–R19).

Two system-preference keys, written by a super-admin through the ordinary
preference endpoint and read publicly as one document. ``site_media`` maps
a purpose the site defines to a public media uuid; the write refuses a
uuid the site could not fetch.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import json

from club_server.db.models.user import User, UserStatus
from club_server.mailer.sender import ConsoleEmailSender
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_media_row


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _pending_user(db: AsyncSession, username: str, email: str) -> None:
    db.add(
        User(
            username=username,
            email=email,
            password=AuthService.hash_password("pw12345678"),
            first_name="Pat",
            status=UserStatus.pending.value,
            is_super_admin=0,
            roles=json.dumps({"roles": []}),
            created_at=now_utc_ms(),
        )
    )
    await db.flush()


CLUB_INFO = {
    "name": "Example Club",
    "shortName": "EXC",
    "tagline": {"default": "Train. Play. Compete.", "mr": "..."},
    "phone": "+919000000000",
    "whatsapp": "+919000000000",
    "email": "club@example.com",
    "inquiryEmail": "query@example.com",
    "address": {
        "line1": "1 Example Street",
        "city": "Pune",
        "state": "MH",
        "postalCode": "000000",
    },
    "social": {"instagram": "https://instagram.com/exampleclub"},
}


@pytest.fixture(autouse=True)
def _clear_outbox():
    ConsoleEmailSender.clear()
    yield
    ConsoleEmailSender.clear()


@pytest.mark.asyncio
@pytest.mark.requirement("public:R16")
async def test_should_read_empty_documents_when_nothing_is_set(client: AsyncClient):
    response = await client.get("/v1/public/club-info")
    assert response.status_code == 200
    assert response.json() == {"clubInfo": {}, "siteMedia": {}}


@pytest.mark.asyncio
@pytest.mark.requirement("public:R17")
async def test_should_store_and_publish_club_info_as_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.patch(
        "/v1/admin/preferences/club_info",
        json={"value": CLUB_INFO},
        headers=_auth(admin),
    )
    assert response.status_code == 200, response.text

    response = await client.get("/v1/public/club-info")
    assert response.status_code == 200
    assert response.json()["clubInfo"] == CLUB_INFO
    assert response.json()["siteMedia"] == {}


@pytest.mark.asyncio
@pytest.mark.requirement("public:R17")
async def test_should_reject_club_info_that_is_not_an_object(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    for bad in ("just a string", ["a", "list"], 42):
        response = await client.patch(
            "/v1/admin/preferences/club_info", json={"value": bad}, headers=_auth(admin)
        )
        assert response.status_code == 422, bad
        assert response.json()["detail"]["code"] == "INVALID_PREFERENCE_VALUE"


@pytest.mark.asyncio
@pytest.mark.requirement("public:R18")
async def test_should_accept_site_media_only_when_every_uuid_is_public_media(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    public_uuid = await create_media_row(db_session, uploaded_by="admin", public=True)
    private_uuid = await create_media_row(db_session, uploaded_by="admin", public=False)
    video_uuid = await create_media_row(
        db_session, uploaded_by="admin", public=True, media_type="video"
    )
    await db_session.commit()

    ok = {"landing_background": video_uuid, "page_hero_default": public_uuid}
    response = await client.patch(
        "/v1/admin/preferences/site_media", json={"value": ok}, headers=_auth(admin)
    )
    assert response.status_code == 200, response.text

    # Stored as uuids, published as descriptors: the landing slot is a video
    # here and a still on another deployment, and a uuid does not say which
    # (#424).
    published = (await client.get("/v1/public/club-info")).json()["siteMedia"]
    assert set(published) == set(ok)
    assert published["landing_background"]["uuid"] == video_uuid
    assert published["landing_background"]["mimeType"].startswith("video/")
    assert published["page_hero_default"]["uuid"] == public_uuid
    assert published["page_hero_default"]["mimeType"].startswith("image/")

    for bad in (
        {"logo": private_uuid},
        {"logo": "00000000-0000-0000-0000-000000000000"},
        {"logo": 7},
        ["not", "a", "map"],
    ):
        response = await client.patch(
            "/v1/admin/preferences/site_media",
            json={"value": bad},
            headers=_auth(admin),
        )
        assert response.status_code == 422, bad
        assert response.json()["detail"]["code"] in (
            "SITE_MEDIA_NOT_PUBLIC",
            "INVALID_PREFERENCE_VALUE",
        )
    # The refused writes left the stored map untouched.
    again = (await client.get("/v1/public/club-info")).json()["siteMedia"]
    assert {k: v["uuid"] for k, v in again.items()} == ok


@pytest.mark.asyncio
@pytest.mark.requirement("public:R18")
async def test_should_clear_site_media_with_an_empty_map(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    public_uuid = await create_media_row(db_session, uploaded_by="admin", public=True)
    await db_session.commit()
    await client.patch(
        "/v1/admin/preferences/site_media",
        json={"value": {"logo": public_uuid}},
        headers=_auth(admin),
    )

    response = await client.patch(
        "/v1/admin/preferences/site_media", json={"value": {}}, headers=_auth(admin)
    )
    assert response.status_code == 200
    assert (await client.get("/v1/public/club-info")).json()["siteMedia"] == {}


@pytest.mark.asyncio
@pytest.mark.requirement("public:R19")
async def test_should_brand_emails_with_the_club_info_name_when_set(
    client: AsyncClient, db_session: AsyncSession
):
    """The mailer reads the club's name from club_info; the env value is the fallback."""
    admin = await create_admin_user(db_session)
    await _pending_user(db_session, "pending_one", "pending@example.com")
    await db_session.commit()

    approved = await client.post(
        "/v1/users/by_id/pending_one/approve", headers=_auth(admin)
    )
    assert approved.status_code == 200, approved.text
    assert len(ConsoleEmailSender.outbox) == 1
    assert "Test Club" in ConsoleEmailSender.outbox[0].html
    ConsoleEmailSender.clear()

    await client.patch(
        "/v1/admin/preferences/club_info",
        json={"value": {"name": "Polar Bears Club", "shortName": "Bears"}},
        headers=_auth(admin),
    )
    await _pending_user(db_session, "pending_two", "two@example.com")
    await db_session.commit()
    approved = await client.post(
        "/v1/users/by_id/pending_two/approve", headers=_auth(admin)
    )
    assert approved.status_code == 200, approved.text
    assert len(ConsoleEmailSender.outbox) == 1
    sent = ConsoleEmailSender.outbox[0]
    assert "Polar Bears Club" in sent.html
    assert "Bears" in sent.subject
    assert "Test Club" not in sent.html
