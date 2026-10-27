"""Extended profile fields on the user profile (#92, users R63–R66).

The fields ride on the ordinary profile edit and the private profile. They
are stored and returned whatever ``EXTENDED_PROFILE_ENABLED`` says; the
setting only tells the app whether to show them.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .extended_profile_helpers import (
    EXTENDED,
    MIDNIGHT_2024_06_01,
    auth,
    patch_user,
    private,
    write_options,
)
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
    create_user_with_status,
)

EXTENDED_KEYS = set(EXTENDED) | {"membershipDateUtc"}
LIST_KEYS = {"preferredPositions", "preferredLanguages"}


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
async def test_should_store_extended_fields_when_member_edits_own_profile(
    client: AsyncClient, db_session: AsyncSession, extended_profile_enabled: None
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)

    response = await patch_user(client, member, "mira", EXTENDED)

    assert response.status_code == 200, response.text
    for key, value in EXTENDED.items():
        assert response.json()[key] == value
    own = await private(client, member, "mira")
    seen_by_admin = await private(client, admin, "mira")
    for key, value in EXTENDED.items():
        assert own[key] == value
        assert seen_by_admin[key] == value


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
async def test_should_store_extended_fields_when_admin_edits_a_member(
    client: AsyncClient, db_session: AsyncSession, extended_profile_enabled: None
):
    super_admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session, "ravi")
    member = await create_member_user(db_session, "mira")
    await write_options(client, super_admin)

    response = await patch_user(client, regular, "mira", EXTENDED)

    assert response.status_code == 200, response.text
    own = await private(client, member, "mira")
    for key, value in EXTENDED.items():
        assert own[key] == value


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
async def test_should_store_extended_fields_when_extended_profile_is_off(
    client: AsyncClient, db_session: AsyncSession
):
    """The schema does not vary with configuration: off, the fields still
    store and read back."""
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)

    response = await patch_user(client, member, "mira", EXTENDED)

    assert response.status_code == 200, response.text
    own = await private(client, member, "mira")
    for key, value in EXTENDED.items():
        assert own[key] == value


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
async def test_should_clear_one_extended_field_when_null_is_sent(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)
    first = await patch_user(client, member, "mira", EXTENDED)
    assert first.status_code == 200, first.text

    cleared = await patch_user(
        client, member, "mira", {"guardian": None, "nationality": None}
    )

    assert cleared.status_code == 200, cleared.text
    own = await private(client, member, "mira")
    assert own["guardian"] is None
    assert own["nationality"] is None
    assert own["clubDistrict"] == EXTENDED["clubDistrict"]
    assert own["preferredPositions"] == EXTENDED["preferredPositions"]


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
async def test_should_return_blank_extended_fields_when_never_set(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")

    own = await private(client, member, "mira")

    for key in EXTENDED_KEYS:
        if key in LIST_KEYS:
            assert own[key] == [], key  # lists read back empty (#106)
        else:
            assert own[key] is None, key


@pytest.mark.requirement("users:R63a")
@pytest.mark.asyncio
async def test_should_show_extended_fields_to_staff_and_not_on_the_basic_profile(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "cora")
    member = await create_member_user(db_session, "mira")
    other = await create_member_user(db_session, "otto")
    await write_options(client, admin)
    written = await patch_user(client, member, "mira", EXTENDED)
    assert written.status_code == 200, written.text

    seen_by_coach = await private(client, coach, "mira")
    basic = await client.get("/v1/users/by_id/mira", headers=auth(other))

    assert seen_by_coach["clubDistrict"] == EXTENDED["clubDistrict"]
    assert seen_by_coach["guardian"] == EXTENDED["guardian"]
    assert basic.status_code == 200, basic.text
    assert not EXTENDED_KEYS & set(basic.json())


@pytest.mark.requirement("users:R63b")
@pytest.mark.asyncio
@pytest.mark.parametrize("editor", ["coach", "member"])
async def test_should_refuse_extended_edit_when_caller_edits_another_user(
    client: AsyncClient, db_session: AsyncSession, editor: str
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "mira")
    token = (
        await create_coach_user(db_session, "cora")
        if editor == "coach"
        else await create_member_user(db_session, "otto")
    )
    await db_session.commit()

    response = await patch_user(client, token, "mira", {"clubDistrict": "Elsewhere"})

    assert response.status_code == 403, response.text
    assert (await private(client, admin, "mira"))["clubDistrict"] is None


@pytest.mark.requirement("users:R63b")
@pytest.mark.asyncio
async def test_should_refuse_extended_edit_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "mira")
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/mira", json={"clubDistrict": "Elsewhere"}
    )

    assert response.status_code == 401, response.text
    assert (await private(client, admin, "mira"))["clubDistrict"] is None


@pytest.mark.requirement("users:R64")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preferredPositions", ["Forward", "Striker"]),
        ("preferredLanguages", ["Klingon"]),
    ],
)
async def test_should_refuse_a_choice_when_it_is_not_a_club_option(
    client: AsyncClient, db_session: AsyncSession, field: str, value: list[str]
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)
    await db_session.commit()

    response = await patch_user(
        client, member, "mira", {field: value, "clubDistrict": "Pune"}
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_PROFILE_OPTION"
    assert detail["field"] == field
    assert detail["value"] == value[-1]
    own = await private(client, member, "mira")
    assert own[field] == []
    assert own["clubDistrict"] is None


@pytest.mark.requirement("users:R64")
@pytest.mark.asyncio
async def test_should_refuse_a_position_when_the_club_has_no_options(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await db_session.commit()

    response = await patch_user(
        client, member, "mira", {"preferredPositions": ["Forward"]}
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_PROFILE_OPTION"
    assert (await private(client, member, "mira"))["preferredPositions"] == []


@pytest.mark.requirement("users:R64")
@pytest.mark.asyncio
async def test_should_keep_a_choice_when_its_option_is_removed_later(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)
    chosen = await patch_user(
        client,
        member,
        "mira",
        {
            "preferredPositions": ["Forward", "Goalie"],
            "preferredLanguages": ["Marathi"],
        },
    )
    assert chosen.status_code == 200, chosen.text
    await write_options(
        client,
        admin,
        # Marathi goes, so Forward's label loses its Marathi (#133).
        {
            "positions": [{"value": "Forward", "label": {"en": "Forward"}}],
            "languages": ["English"],
        },
    )

    kept = await private(client, member, "mira")
    resent = await patch_user(
        client,
        member,
        "mira",
        {
            "preferredPositions": ["Forward", "Goalie"],
            "preferredLanguages": ["Marathi"],
            "clubDistrict": "Pune",
        },
    )

    assert kept["preferredPositions"] == ["Forward", "Goalie"]
    assert kept["preferredLanguages"] == ["Marathi"]
    assert resent.status_code == 200, resent.text
    after = await private(client, member, "mira")
    assert after["preferredPositions"] == ["Forward", "Goalie"]
    assert after["clubDistrict"] == "Pune"


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
async def test_should_store_several_positions_and_languages_when_member_chooses_them(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)

    response = await patch_user(
        client,
        member,
        "mira",
        {
            "preferredPositions": ["Goalie", "Forward"],
            "preferredLanguages": ["Marathi", "English"],
        },
    )

    assert response.status_code == 200, response.text
    own = await private(client, member, "mira")
    assert own["preferredPositions"] == ["Goalie", "Forward"]
    assert own["preferredLanguages"] == ["Marathi", "English"]
    staff = await private(client, admin, "mira")
    assert staff["preferredPositions"] == ["Goalie", "Forward"]


@pytest.mark.requirement("users:R63")
@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", [None, []])
async def test_should_clear_the_choices_when_null_or_an_empty_list_is_sent(
    client: AsyncClient, db_session: AsyncSession, cleared: list[str] | None
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)
    chosen = await patch_user(
        client, member, "mira", {"preferredPositions": ["Forward", "Goalie"]}
    )
    assert chosen.status_code == 200, chosen.text

    response = await patch_user(client, member, "mira", {"preferredPositions": cleared})

    assert response.status_code == 200, response.text
    assert (await private(client, member, "mira"))["preferredPositions"] == []


@pytest.mark.requirement("users:R64")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preferredPositions", ["Forward", "Forward"]),
        ("preferredLanguages", ["Marathi", "English", "Marathi"]),
    ],
)
async def test_should_refuse_a_choice_list_when_a_value_repeats(
    client: AsyncClient, db_session: AsyncSession, field: str, value: list[str]
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await write_options(client, admin)
    await db_session.commit()

    response = await patch_user(client, member, "mira", {field: value})

    assert response.status_code == 422, response.text
    assert (await private(client, member, "mira"))[field] == []


@pytest.mark.requirement("users:R65")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guardian",
    [
        {"name": "Sunita", "relation": "Mother", "phone": "+91 98765 43210 ext 99"},
        {"name": "Sunita", "relation": "Mother", "email": "s@example.com"},
    ],
)
async def test_should_refuse_a_guardian_when_phone_or_shape_is_invalid(
    client: AsyncClient, db_session: AsyncSession, guardian: dict[str, str]
):
    _ = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    await db_session.commit()

    response = await patch_user(client, member, "mira", {"guardian": guardian})

    assert response.status_code == 422, response.text
    assert (await private(client, member, "mira"))["guardian"] is None


@pytest.mark.requirement("users:R65")
@pytest.mark.asyncio
async def test_should_store_a_guardian_when_only_some_parts_are_given(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")

    response = await patch_user(
        client, member, "mira", {"guardian": {"name": "Sunita"}}
    )

    assert response.status_code == 200, response.text
    assert (await private(client, member, "mira"))["guardian"] == {
        "name": "Sunita",
        "relation": None,
        "phone": None,
    }


@pytest.mark.requirement("users:R66")
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [UserStatus.registered, UserStatus.pending])
async def test_should_store_own_extended_fields_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    admin = await create_admin_user(db_session)
    newbie = await create_user_with_status(db_session, "newbie", status)
    await write_options(client, admin)

    response = await patch_user(client, newbie, "newbie", EXTENDED)

    assert response.status_code == 200, response.text
    me = await client.get("/v1/auth/me", headers=auth(newbie))
    assert me.status_code == 200, me.text
    for key, value in EXTENDED.items():
        assert me.json()[key] == value
    assert me.json()["status"] == status.value


OWN_PROFILE: dict[str, Any] = {
    "firstName": "Neha",
    "phone": "+919812345678",
    "address": {
        "addrLine1": "12 Lake Road",
        "addrLine2": None,
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411001",
    },
    "bio": "Played roller hockey at school.",
}


@pytest.mark.requirement("users:R66")
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [UserStatus.registered, UserStatus.pending])
async def test_should_store_own_profile_fields_when_user_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    """Issue 123: name, contact, address and bio are theirs to fill before
    approval, so the application form they download is complete."""
    admin = await create_admin_user(db_session)
    newbie = await create_user_with_status(db_session, "newbie", status)
    await db_session.commit()

    response = await patch_user(client, newbie, "newbie", OWN_PROFILE)

    assert response.status_code == 200, response.text
    me = await client.get("/v1/auth/me", headers=auth(newbie))
    assert me.status_code == 200, me.text
    stored = await private(client, admin, "newbie")
    for key, value in OWN_PROFILE.items():
        assert me.json()[key] == value
        assert stored[key] == value
    assert me.json()["status"] == status.value


@pytest.mark.requirement("users:R66")
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [UserStatus.registered, UserStatus.pending])
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"gender": "female"}, "gender"),
        ({"dateOfBirthUtc": MIDNIGHT_2024_06_01}, "dateOfBirthUtc"),
        ({"bio": "Hello", "gender": "female"}, "gender"),
    ],
)
async def test_should_refuse_gender_and_birth_date_when_unapproved_user_edits_own(
    client: AsyncClient,
    db_session: AsyncSession,
    status: UserStatus,
    body: dict[str, Any],
    field: str,
):
    """Issue 123: gender and date of birth stay an admin's to change."""
    admin = await create_admin_user(db_session)
    newbie = await create_user_with_status(db_session, "newbie", status)
    await db_session.commit()

    response = await patch_user(client, newbie, "newbie", body)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "PROTECTED_FIELDS"
    assert response.json()["detail"]["fields"] == [field]
    after = await private(client, admin, "newbie")
    assert after["gender"] is None
    assert after["dateOfBirthUtc"] is None
    assert after["bio"] is None


@pytest.mark.requirement("users:R66")
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [UserStatus.registered, UserStatus.pending])
@pytest.mark.parametrize(
    "body",
    [{"bio": "Hello"}, {"clubDistrict": "Pune"}, {"phone": "+919812345678"}],
)
async def test_should_refuse_an_unapproved_user_when_editing_another_profile(
    client: AsyncClient,
    db_session: AsyncSession,
    status: UserStatus,
    body: dict[str, str],
):
    admin = await create_admin_user(db_session)
    newbie = await create_user_with_status(db_session, "newbie", status)
    _ = await create_member_user(db_session, "mira")
    await db_session.commit()
    before = await private(client, admin, "mira")

    response = await patch_user(client, newbie, "mira", body)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "ACCOUNT_NOT_ACTIVE"
    after = await private(client, admin, "mira")
    assert after["bio"] is None
    assert after["clubDistrict"] is None
    assert after["phone"] == before["phone"]


@pytest.mark.requirement("users:R66")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(UserStatus.blocked, "ACCOUNT_BLOCKED"), (UserStatus.left, "ACCOUNT_LEFT")],
)
async def test_should_refuse_a_blocked_or_left_user_when_editing_own_profile(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus, code: str
):
    """Issue 123: only `registered` and `pending` gain the self-edit; a
    blocked or departed account is refused before the edit is looked at."""
    admin = await create_admin_user(db_session)
    gone = await create_user_with_status(db_session, "gone", status)
    await db_session.commit()

    response = await patch_user(client, gone, "gone", {"phone": "+919812345678"})

    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == code
    assert (await private(client, admin, "gone"))["phone"] is None
