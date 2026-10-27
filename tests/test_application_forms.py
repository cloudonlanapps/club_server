"""Members' forms and the admin's preview, as PDFs (#93, R10, R11, R15–R21)."""

from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_today, day_to_date
from club_server.db.models.user import User, UserStatus

from .application_helpers import (
    APPLICATIONS,
    assert_pdf,
    auth,
    create_applicant,
    create_application,
    day,
    move,
    page_texts,
    published_application,
    template_body,
)
from .extended_profile_helpers import write_options
from .helpers import create_admin_user, create_coach_user, create_regular_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

GUARDIAN_HEADING = "Parent / guardian contact"


async def own_form(
    client: AsyncClient, token: str, application_id: int, category: int | None = None
):
    return await client.get(
        f"{APPLICATIONS}/{application_id}/form",
        params={} if category is None else {"category": category},
        headers=auth(token),
    )


async def member_form(
    client: AsyncClient, token: str, application_id: int, username: str
):
    return await client.get(
        f"{APPLICATIONS}/{application_id}/form/{username}", headers=auth(token)
    )


# R15 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R15")
@pytest.mark.asyncio
async def test_should_fill_the_form_from_the_profile_when_member_downloads_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await write_options(client, admin)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi")
    user = await db_session.get(User, "ravi")
    assert user is not None
    user.nationality, user.club_district = "Indian", "City District"
    user.preferred_position = "Forward"
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    text = assert_pdf(response, published["id"], "ravi")
    signing_day = f"{day_to_date(club_today()):%d / %m / %Y}"
    for expected in (
        "EXAMPLE CLUB",
        "Selection Trial — Registration Form",
        "City Rink",
        "Ravi Kumar Sharma",
        "14 / 05 / 2015",
        "Indian",
        "City District",
        "Forward",
        "Past experience — any skating sport",  # the template's label override
        "Play fair, play hard.",
        "Tournament matches will take place from 19–21 November 2026 at City Rink.",
        signing_day,
        "ravi · downloaded",
    ):
        assert expected in text, expected
    pages = page_texts(response.content)
    assert len(pages) == 2  # the form, then the terms
    assert "Rules & regulations" in pages[1]


@pytest.mark.requirement("applications:R15")
@pytest.mark.asyncio
async def test_should_print_a_position_in_english_when_it_has_no_second_language(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await write_options(
        client,
        admin,
        {
            "positions": [
                {"value": "fwd", "label": {"en": "Forward"}},
                {"value": "gk", "label": {"en": "Goalie", "mr": "गोलकीपर"}},
            ],
            "languages": [],
        },
    )
    published = await published_application(client, admin)  # prints en + mr
    assert published["status"] == "published"
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    text = assert_pdf(response, published["id"], "ravi")
    assert "Forward" in text
    assert "Goalie" in text


@pytest.mark.requirement("applications:R16")
@pytest.mark.asyncio
async def test_should_put_the_signing_part_on_page_two_when_the_form_is_long(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = template_body()
    body["selections"] = [
        {
            "en": f"I will attend practice session {i} every morning of the camp week.",
            "mr": f"मी शिबिराच्या आठवड्यात दररोज सकाळी सराव सत्र {i} ला उपस्थित राहीन.",
        }
        for i in range(12)
    ]
    published = await published_application(client, admin, body)
    member = await create_applicant(db_session, "ravi")

    response = await own_form(client, member, published["id"])

    assert published["layout"]["name"] == "signing part on page 2"
    _ = assert_pdf(response, published["id"], "ravi")
    pages = page_texts(response.content)
    assert "Declaration" not in pages[0]
    assert "Declaration" in pages[1]


# R21 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R21")
@pytest.mark.asyncio
async def test_should_print_the_guardian_block_when_member_is_a_minor(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(
        client, admin, {**template_body(), "ageCategories": []}
    )
    minor = await create_applicant(db_session, "ravi")
    adult = await create_applicant(db_session, "asha", born=date(1990, 1, 1))
    guarded = await create_applicant(
        db_session,
        "dev",
        born=date(1990, 1, 1),
        guardian={"name": "Meera Iyer", "phone": "+911234"},
    )
    await db_session.commit()

    minor_text = assert_pdf(
        await own_form(client, minor, published["id"]), published["id"], "ravi"
    )
    adult_text = assert_pdf(
        await own_form(client, adult, published["id"]), published["id"], "asha"
    )
    guarded_text = assert_pdf(
        await own_form(client, guarded, published["id"]), published["id"], "dev"
    )

    assert GUARDIAN_HEADING in minor_text
    assert GUARDIAN_HEADING not in adult_text
    assert "Parent / guardian signature" not in adult_text
    assert GUARDIAN_HEADING in guarded_text and "Meera Iyer" in guarded_text


# R10, R11 -----------------------------------------------------------------


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [UserStatus.registered, UserStatus.pending])
async def test_should_give_the_form_when_member_is_not_yet_approved(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi", status=status)

    response = await own_form(client, member, published["id"])

    assert "Ravi Kumar Sharma" in assert_pdf(response, published["id"], "ravi")


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
async def test_should_refuse_the_form_when_member_has_no_identity_document(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi", identity_document=False)
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "IDENTITY_DOCUMENT_REQUIRED"


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "saved"])
async def test_should_hide_the_form_when_the_template_is_not_published(
    client: AsyncClient, db_session: AsyncSession, state: str
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    if state == "saved":
        assert (await move(client, admin, created["id"], "save")).status_code == 200
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()

    response = await own_form(client, member, created["id"])

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "APPLICATION_NOT_FOUND"


@pytest.mark.requirement("applications:R11")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("born", "gender"),
    [(date(1990, 1, 1), "male"), (date(2012, 6, 1), "male"), (None, "male")],
)
async def test_should_refuse_the_form_when_no_age_category_matches(
    client: AsyncClient, db_session: AsyncSession, born: date | None, gender: str
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi", born=born, gender=gender)
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_NOT_ELIGIBLE"
    assert detail["message"] == "not eligible for this application"


@pytest.mark.requirement("applications:R11")
@pytest.mark.asyncio
async def test_should_give_the_form_when_a_category_matches_birth_and_gender(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    girl = await create_applicant(
        db_session, "isha", born=date(2012, 6, 1), gender="female"
    )

    response = await own_form(client, girl, published["id"])

    assert "Under-15 Girls" in assert_pdf(response, published["id"], "isha")


@pytest.mark.requirement("applications:R11")
@pytest.mark.asyncio
async def test_should_give_the_form_to_anyone_when_template_has_no_categories(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(
        client, admin, {**template_body(), "ageCategories": []}
    )
    member = await create_applicant(db_session, "ravi", born=None, gender=None)

    response = await own_form(client, member, published["id"])

    _ = assert_pdf(response, published["id"], "ravi")


# R11a: a member who fits several categories chooses one -------------------


def overlapping_body() -> dict[str, Any]:
    """The template with two overlapping categories: U12 inside U14."""
    body = template_body()
    body["ageCategories"] = [
        {
            "label": {"en": "U12", "mr": "U12"},
            "bornOnOrAfterUtc": day(2013, 1, 1),
            "bornOnOrBeforeUtc": day(2014, 12, 31),
        },
        {
            "label": {"en": "U14", "mr": "U14"},
            "bornOnOrAfterUtc": day(2011, 1, 1),
            "bornOnOrBeforeUtc": day(2014, 12, 31),
        },
    ]
    return body


async def read_as(client: AsyncClient, token: str, application_id: int):
    response = await client.get(f"{APPLICATIONS}/{application_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.requirement("applications:R11a")
@pytest.mark.asyncio
async def test_should_list_every_fitting_category_when_member_reads_the_template(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    both = await create_applicant(db_session, "ravi", born=date(2014, 5, 1))
    older = await create_applicant(db_session, "omar", born=date(2012, 3, 1))
    young = await create_applicant(db_session, "yash", born=date(2016, 1, 1))
    await db_session.commit()

    assert (await read_as(client, both, published["id"]))["fittingCategories"] == [
        0,
        1,
    ]
    assert (await read_as(client, older, published["id"]))["fittingCategories"] == [1]
    assert (await read_as(client, young, published["id"]))["fittingCategories"] == []
    assert (await read_as(client, admin, published["id"]))["fittingCategories"] is None


@pytest.mark.requirement("applications:R11a")
@pytest.mark.asyncio
async def test_should_ask_for_a_category_when_member_fits_several_and_names_none(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    member = await create_applicant(db_session, "ravi", born=date(2014, 5, 1))
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_CATEGORY_REQUIRED"
    assert detail["details"] == {"categories": [0, 1]}


@pytest.mark.requirement("applications:R11a")
@pytest.mark.asyncio
async def test_should_give_the_form_when_member_chooses_a_fitting_category(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    member = await create_applicant(db_session, "ravi", born=date(2014, 5, 1))
    await db_session.commit()

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form?category=1", headers=auth(member)
    )

    assert "U14" in assert_pdf(response, published["id"], "ravi")


@pytest.mark.requirement("applications:R11a")
@pytest.mark.asyncio
async def test_should_give_the_form_without_a_choice_when_member_fits_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    member = await create_applicant(db_session, "omar", born=date(2012, 3, 1))
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    assert "U14" in assert_pdf(response, published["id"], "omar")


@pytest.mark.requirement("applications:R11a")
@pytest.mark.asyncio
@pytest.mark.parametrize("category", [0, 5])
async def test_should_refuse_the_form_when_chosen_category_does_not_fit(
    client: AsyncClient, db_session: AsyncSession, category: int
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    member = await create_applicant(db_session, "omar", born=date(2012, 3, 1))
    await db_session.commit()

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form?category={category}",
        headers=auth(member),
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_CATEGORY_NOT_FITTING"
    assert detail["details"] == {"categories": [1]}


@pytest.mark.requirement("applications:R11a")
@pytest.mark.asyncio
async def test_should_refuse_the_form_when_member_fits_no_overlapping_category(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    member = await create_applicant(db_session, "yash", born=date(2016, 1, 1))
    await db_session.commit()

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form?category=0", headers=auth(member)
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "APPLICATION_NOT_ELIGIBLE"


@pytest.mark.requirement("applications:R20")
@pytest.mark.asyncio
async def test_should_take_the_category_when_admin_downloads_a_members_form(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin, overlapping_body())
    _ = await create_applicant(db_session, "ravi", born=date(2014, 5, 1))
    await db_session.commit()

    unchosen = await member_form(client, admin, published["id"], "ravi")
    chosen = await client.get(
        f"{APPLICATIONS}/{published['id']}/form/ravi?category=0", headers=auth(admin)
    )

    assert unchosen.status_code == 422, unchosen.text
    assert unchosen.json()["detail"]["code"] == "APPLICATION_CATEGORY_REQUIRED"
    assert "U12" in assert_pdf(chosen, published["id"], "ravi")


# R20 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R20")
@pytest.mark.asyncio
async def test_should_give_an_admin_any_members_form_when_published(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    published = await published_application(client, admin)
    _ = await create_applicant(db_session, "ravi", identity_document=False)

    response = await member_form(client, regular, published["id"], "ravi")

    assert "Ravi Kumar Sharma" in assert_pdf(response, published["id"], "ravi")


@pytest.mark.requirement("applications:R20")
@pytest.mark.asyncio
async def test_should_refuse_a_members_form_when_template_is_not_published(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    _ = await create_applicant(db_session, "ravi")
    await db_session.commit()

    response = await member_form(client, admin, created["id"], "ravi")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "APPLICATION_NOT_PUBLISHED"


@pytest.mark.requirement("applications:R20")
@pytest.mark.asyncio
async def test_should_answer_not_found_when_the_member_does_not_exist(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)

    response = await member_form(client, admin, published["id"], "nobody")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("applications:R20")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member"])
async def test_should_refuse_another_members_form_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    _ = await create_applicant(db_session, "ravi")
    token = (
        await create_coach_user(db_session)
        if role == "coach"
        else await create_applicant(db_session, "asha")
    )
    await db_session.commit()

    response = await member_form(client, token, published["id"], "ravi")

    assert response.status_code == 403


FORTY_POSITIONS = {
    "positions": [
        {"value": f"pos{i}", "label": {"en": f"Position {i}"}} for i in range(40)
    ],
    "languages": ["English"],
}


@pytest.mark.requirement("applications:R14")
@pytest.mark.requirement("applications:R20")
@pytest.mark.asyncio
async def test_should_draw_published_forms_unchanged_when_positions_change_later(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)  # no positions yet
    _ = await create_applicant(db_session, "ravi")
    asha = await create_applicant(db_session, "asha", gender="female")
    await db_session.commit()
    before = {
        "ravi": await member_form(client, admin, published["id"], "ravi"),
        "asha": await own_form(client, asha, published["id"], category=1),
    }

    await write_options(client, admin, FORTY_POSITIONS)
    after = {
        "ravi": await member_form(client, admin, published["id"], "ravi"),
        "asha": await own_form(client, asha, published["id"], category=1),
    }

    for username in ("ravi", "asha"):
        text = assert_pdf(after[username], published["id"], username)
        assert page_texts(after[username].content) == page_texts(
            before[username].content
        )
        assert "Position 39" not in text
        assert published["publishId"] in text


# R23 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R23")
@pytest.mark.asyncio
async def test_should_print_the_publish_id_on_every_page_of_a_form(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()

    response = await own_form(client, member, published["id"])

    _ = assert_pdf(response, published["id"], "ravi")
    pages = page_texts(response.content)
    assert len(pages) == 2
    assert all(published["publishId"] in page for page in pages)


# R19 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R19")
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "saved", "published"])
async def test_should_preview_the_demo_member_when_admin_asks_in_any_state(
    client: AsyncClient, db_session: AsyncSession, state: str
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    for action in ("save", "publish")[
        : {"draft": 0, "saved": 1, "published": 2}[state]
    ]:
        assert (await move(client, admin, created["id"], action)).status_code == 200

    response = await client.get(
        f"{APPLICATIONS}/{created['id']}/preview", headers=auth(admin)
    )

    text = assert_pdf(response, created["id"], "demo.player")
    assert "PREVIEW — not for submission" in text
    assert "Arjun Vikram Deshpande" in text
    assert GUARDIAN_HEADING in text
    assert "Selection Trial — Registration Form" in text


@pytest.mark.requirement("applications:R19")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category",
    [
        {"label": {"en": "Late starters"}, "bornOnOrAfterUtc": day(2010, 9, 1)},
        {
            "label": {"en": "Late starters"},
            "bornOnOrAfterUtc": day(2010, 9, 1),
            "bornOnOrBeforeUtc": day(2011, 12, 31),
        },
    ],
    ids=["after-only", "after-and-before"],
)
async def test_should_preview_when_first_category_starts_after_mid_june(
    client: AsyncClient, db_session: AsyncSession, category: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    body = template_body()
    body["ageCategories"] = [category]
    created = await create_application(client, admin, body)

    response = await client.get(
        f"{APPLICATIONS}/{created['id']}/preview", headers=auth(admin)
    )

    text = assert_pdf(response, created["id"], "demo.player")
    assert "PREVIEW — not for submission" in text
    assert "01 / 09 / 2010" in text  # the demo member's date of birth


@pytest.mark.requirement("applications:R19")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member"])
async def test_should_refuse_the_preview_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    token = (
        await create_coach_user(db_session)
        if role == "coach"
        else await create_applicant(db_session, "ravi")
    )
    await db_session.commit()

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/preview", headers=auth(token)
    )

    assert response.status_code == 403
