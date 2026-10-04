"""Public staff listing curation (#332, public R1–R7).

``users.display_order`` carried two facts on the audited user row: where a
coach sits on the website's staff page and, by sign, whether they are a
guest. Both move to ``public_staff_listing``, curated by admins through
their own endpoints, so reordering a page is no longer a profile edit.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import User, UserStatus
from club_server.services.auth import AuthService
from club_server.utils import generate_public_id, now_utc_ms

from .helpers import create_admin_user, create_coach_user, create_member_user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_WALKIN = {
    "username": "walkin",
    "password": "walkinpass123",
    "firstName": "Walk",
    "lastName": "In",
    "gender": "female",
    "dateOfBirthUtc": 946684800000,
    "phone": "+911111111111",
}


async def _public_coach(
    db: AsyncSession, username: str, *, opted_in: bool = True
) -> str:
    """A coach who has (or has not) opted their profile in; returns a token."""
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
    return AuthService.create_access_token(username, is_super_admin=False)


async def _seed(client: AsyncClient, db_session: AsyncSession) -> str:
    """Admin plus four opted-in coaches; returns the admin token."""
    admin = await create_admin_user(db_session)
    for name in ("suraj", "aagam", "aaryan", "manju"):
        await _public_coach(db_session, name)
    await db_session.commit()
    return admin


async def _curate(client: AsyncClient, admin: str, username: str, **body) -> dict:
    response = await client.put(
        f"/v1/admin/staff-listing/{username}", json=body, headers=_auth(admin)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _names(items: list[dict]) -> list[str]:
    return [i["displayName"] for i in items]


# ── public ordering and filters ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R1")
async def test_should_order_public_staff_by_position_then_unlisted_by_name(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _seed(client, db_session)
    await _curate(client, admin, "aagam", position=20)
    await _curate(client, admin, "suraj", position=10)

    response = await client.get("/v1/public/staff")
    assert response.status_code == 200
    assert _names(response.json()) == ["Suraj", "Aagam", "Aaryan", "Manju"]
    assert all(item["isGuest"] is False for item in response.json())


@pytest.mark.asyncio
@pytest.mark.requirement("public:R1")
async def test_should_list_only_opted_in_coaches_whatever_the_curation(
    client: AsyncClient, db_session: AsyncSession
):
    """The listing row can withhold or order; it can never grant visibility."""
    admin = await _seed(client, db_session)
    await _public_coach(db_session, "private_coach", opted_in=False)
    await db_session.commit()
    await _curate(client, admin, "private_coach", position=1)

    response = await client.get("/v1/public/staff")
    assert "Private_Coach" not in _names(response.json())


@pytest.mark.asyncio
@pytest.mark.requirement("public:R2")
async def test_should_hide_hidden_coach_from_public_staff(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _seed(client, db_session)
    await _curate(client, admin, "manju", is_hidden=True)

    response = await client.get("/v1/public/staff")
    assert "Manju" not in _names(response.json())
    response = await client.get("/v1/public/staff", params={"include_guests": "true"})
    assert "Manju" not in _names(response.json())


@pytest.mark.asyncio
@pytest.mark.requirement("public:R3")
async def test_should_drop_guests_unless_include_guests(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _seed(client, db_session)
    await _curate(client, admin, "aaryan", is_guest=True, position=5)

    default = await client.get("/v1/public/staff")
    assert "Aaryan" not in _names(default.json())

    with_guests = await client.get(
        "/v1/public/staff", params={"include_guests": "true"}
    )
    assert _names(with_guests.json())[0] == "Aaryan"
    guest = with_guests.json()[0]
    assert guest["isGuest"] is True
    assert guest["publicId"] == generate_public_id("aaryan")


# ── admin curation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R4")
async def test_should_upsert_read_and_delete_listing_rows_as_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _seed(client, db_session)

    row = await _curate(client, admin, "suraj", position=10, is_guest=False)
    assert row == {
        "username": "suraj",
        "displayName": "Suraj",
        "isPublicProfile": True,
        "position": 10,
        "isGuest": False,
        "isHidden": False,
    }
    row = await _curate(client, admin, "suraj", is_hidden=True)
    assert row["position"] == 10  # untouched fields keep their value
    assert row["isHidden"] is True

    listing = await client.get("/v1/admin/staff-listing", headers=_auth(admin))
    assert listing.status_code == 200
    assert [r["username"] for r in listing.json()] == ["suraj"]

    deleted = await client.delete("/v1/admin/staff-listing/suraj", headers=_auth(admin))
    assert deleted.status_code == 204
    listing = await client.get("/v1/admin/staff-listing", headers=_auth(admin))
    assert listing.json() == []
    # Uncurated again: back in the public list, last by name.
    public = await client.get("/v1/public/staff")
    assert "Suraj" in _names(public.json())


@pytest.mark.asyncio
@pytest.mark.requirement("public:R4")
async def test_should_refuse_curation_to_non_admins_and_unknown_or_non_coach_targets(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _seed(client, db_session)
    coach = await create_coach_user(db_session, "plain_coach")
    member = await create_member_user(db_session)
    await db_session.commit()

    assert (
        await client.put("/v1/admin/staff-listing/suraj", json={"position": 1})
    ).status_code == 401
    for token in (coach, member):
        response = await client.put(
            "/v1/admin/staff-listing/suraj", json={"position": 1}, headers=_auth(token)
        )
        assert response.status_code == 403
        assert (
            await client.get("/v1/admin/staff-listing", headers=_auth(token))
        ).status_code == 403

    response = await client.put(
        "/v1/admin/staff-listing/nobody", json={"position": 1}, headers=_auth(admin)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    response = await client.put(
        "/v1/admin/staff-listing/member", json={"position": 1}, headers=_auth(admin)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NOT_A_COACH"


@pytest.mark.requirement("notifications:R106")
@pytest.mark.asyncio
@pytest.mark.requirement("public:R5")
async def test_should_not_treat_curation_as_a_profile_edit(
    client: AsyncClient, db_session: AsyncSession
):
    """No profile.changed_by_admin notice, no UPDATE_USER audit; its own audit row instead."""
    admin = await create_admin_user(db_session)
    coach_token = await _public_coach(db_session, "suraj")
    await db_session.commit()

    await _curate(client, admin, "suraj", position=10)

    notices = await client.get("/v1/notifications", headers=_auth(coach_token))
    assert notices.status_code == 200
    assert [n["type"] for n in notices.json()["items"]] == []

    audit = await client.get(
        "/v1/audit_log", params={"action": "update_staff_listing"}, headers=_auth(admin)
    )
    assert audit.status_code == 200
    assert audit.json()["total"] == 1
    assert audit.json()["rows"][0]["target"]["username"] == "suraj"
    audit = await client.get(
        "/v1/audit_log", params={"action": "update_user"}, headers=_auth(admin)
    )
    assert audit.json()["total"] == 0


# ── guests are admin-created ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R6")
async def test_should_create_guest_coach_with_public_profile_when_admin_says_so(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
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
            "bio": "National team coach",
            "isGuest": True,
        },
        headers=_auth(admin),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["isGuest"] is True
    assert body["isPublicProfile"] is True
    assert "coach" in body["roles"]

    listing = await client.get("/v1/admin/staff-listing", headers=_auth(admin))
    assert [(r["username"], r["isGuest"]) for r in listing.json()] == [
        ("guest_amit", True)
    ]

    public = await client.get("/v1/public/staff", params={"include_guests": "true"})
    assert _names(public.json()) == ["Amit Belwal"]
    assert _names((await client.get("/v1/public/staff")).json()) == []


@pytest.mark.asyncio
@pytest.mark.requirement("public:R6")
async def test_should_refuse_is_guest_on_self_registration(
    client: AsyncClient, db_session: AsyncSession
):
    """Only an admin-created account can be a guest; the flag is not a field here."""
    response = await client.post(
        "/v1/auth/register",
        json={**_WALKIN, "isGuest": True},
    )
    assert response.status_code == 422
    assert "extra_forbidden" in str(response.json()["detail"])

    response = await client.post("/v1/auth/register", json=_WALKIN)
    assert response.status_code in (200, 201), response.text
    admin = await create_admin_user(db_session)
    await db_session.commit()
    fetched = await client.get("/v1/users/by_id/walkin", headers=_auth(admin))
    assert fetched.status_code == 200
    assert fetched.json()["isGuest"] is False
    assert fetched.json()["isPublicProfile"] is False


@pytest.mark.asyncio
@pytest.mark.requirement("public:R7")
async def test_should_no_longer_carry_display_order_on_the_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _public_coach(db_session, "suraj")
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/suraj", json={"displayOrder": -1}, headers=_auth(admin)
    )
    assert response.status_code == 422
    assert "extra_forbidden" in str(response.json()["detail"])

    response = await client.patch(
        "/v1/users/by_id/suraj", json={"bio": "x"}, headers=_auth(admin)
    )
    assert response.status_code == 200, response.text
    assert "displayOrder" not in response.json()
    fetched = await client.get("/v1/users/by_id/suraj", headers=_auth(admin))
    assert "displayOrder" not in fetched.json()
    public = await client.get("/v1/public/staff")
    assert _names(public.json()) == ["Suraj"]
