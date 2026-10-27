"""A template's texts keep ``{fact}`` gaps, filled when printed (#126).

Every printed text may hold ``{name}`` gaps in any language. A draft stores
them as written; save refuses a gap that names none of the template's facts
(applications R9b); the member's form, an admin's member form and the
preview fill each gap from the facts in the text's own language (R15a), and
the layout decided on save is measured on the filled texts.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .application_helpers import (
    APPLICATIONS,
    assert_pdf,
    auth,
    create_applicant,
    create_application,
    day,
    get_application,
    move,
    published_application,
    template_body,
)
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

UNKNOWN_FACT = "APPLICATION_UNKNOWN_FACT"


def gapped_body() -> dict[str, Any]:
    """The test template with facts used through gaps in many of its texts.

    The Marathi venue is Latin text so the PDF's extracted text can show it
    was printed from the Marathi value.
    """
    body = template_body()
    body["facts"]["venue"] = {
        "type": "name",
        "value": {"en": "City Rink", "mr": "Rink-MR"},
    }
    body["facts"]["fee"] = {"type": "amount", "value": 150000}
    body["subtitle"] = {"en": "{camp}", "mr": "{camp}"}
    body["header"][0] = {"en": "Venue: {venue}", "mr": "ठिकाण: {venue}"}
    body["selections"] = [
        {
            "en": "I will attend the camp from {camp} at {venue}.",
            "mr": "मी {camp} दरम्यान {venue} येथे शिबिराला उपस्थित राहीन.",
        }
    ]
    body["terms"][1]["body"] = {
        "en": "A fee of ₹{fee} is payable. Matches: {matches}.",
        "mr": "₹{fee} शुल्क. सामने: {matches}.",
    }
    return body


async def preview(client: AsyncClient, admin: str, application_id: int) -> Response:
    return await client.get(
        f"{APPLICATIONS}/{application_id}/preview", headers=auth(admin)
    )


# R6: stored as written ------------------------------------------------------


@pytest.mark.requirement("applications:R6")
@pytest.mark.asyncio
async def test_should_store_texts_with_gaps_unchanged_when_a_draft_is_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = gapped_body()
    body["organisation"] = {"en": "{club} {{literal}} {Not_a_gap} { x }"}

    created = await create_application(client, admin, body)

    read = (await get_application(client, admin, created["id"])).json()
    for field in ("organisation", "subtitle", "header", "selections", "terms"):
        assert read[field] == body[field], field


@pytest.mark.requirement("applications:R6")
@pytest.mark.asyncio
async def test_should_store_a_gap_naming_no_fact_when_a_draft_is_edited(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    declaration = {"en": "I agree, {nobody}.", "mr": "मला मान्य, {nobody}."}

    response = await client.patch(
        f"{APPLICATIONS}/{created['id']}",
        json={"declaration": declaration},
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    read = (await get_application(client, admin, created["id"])).json()
    assert read["declaration"] == declaration
    assert read["status"] == "draft"


# R9b: save refuses an unknown fact ------------------------------------------


@pytest.mark.requirement("applications:R9b")
@pytest.mark.asyncio
async def test_should_refuse_save_when_a_gap_names_no_fact_in_any_text(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = gapped_body()
    body["organisation"] = {"en": "{club}", "mr": "{club}"}
    body["title"]["mr"] = "{season} निवड चाचणी"  # in Marathi only
    body["header"][1][1] = {"en": "Matches: {match_dates}", "mr": "सामने: {matches}"}
    body["selections"].append({"en": "{kit} and {kit}", "mr": "{kit}"})
    body["declaration"]["en"] += " {camp} {oath}"
    body["formFooter"] = {"en": "{motto}", "mr": "ठीक"}
    body["terms"][0]["heading"] = {"en": "{rule_one}", "mr": "ओळखपत्र"}
    body["terms"][2]["body"] = {"en": "Bring {kit}.", "mr": "{kit} आणा."}
    body["termsNotes"][1]["attribution"] = {"en": "— {coach}"}
    body["termsNotes"][0]["text"]["mr"] = "{rules_note}"
    created = await create_application(client, admin, body)

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == UNKNOWN_FACT
    assert detail["details"]["gaps"] == [
        {"place": "organisation", "language": "en", "gap": "club"},
        {"place": "organisation", "language": "mr", "gap": "club"},
        {"place": "title", "language": "mr", "gap": "season"},
        {"place": "header[1][1]", "language": "en", "gap": "match_dates"},
        {"place": "selections[1]", "language": "en", "gap": "kit"},
        {"place": "selections[1]", "language": "mr", "gap": "kit"},
        {"place": "declaration", "language": "en", "gap": "oath"},
        {"place": "form_footer", "language": "en", "gap": "motto"},
        {"place": "terms[0].heading", "language": "en", "gap": "rule_one"},
        {"place": "terms[2].body", "language": "en", "gap": "kit"},
        {"place": "terms[2].body", "language": "mr", "gap": "kit"},
        {"place": "terms_notes[0].text", "language": "mr", "gap": "rules_note"},
        {"place": "terms_notes[1].attribution", "language": "en", "gap": "coach"},
    ]
    read = (await get_application(client, admin, created["id"])).json()
    assert read["status"] == "draft"
    assert read["layout"] is None


@pytest.mark.requirement("applications:R9b")
@pytest.mark.asyncio
async def test_should_refuse_save_when_a_gap_names_no_fact_in_the_marathi_text_only(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = gapped_body()
    body["selections"][0]["mr"] = "मी {camp_dates} दरम्यान शिबिराला उपस्थित राहीन."
    created = await create_application(client, admin, body)

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == UNKNOWN_FACT
    assert detail["details"]["gaps"] == [
        {"place": "selections[0]", "language": "mr", "gap": "camp_dates"}
    ]
    assert "camp_dates" in detail["message"]
    assert (await get_application(client, admin, created["id"])).json()[
        "status"
    ] == "draft"


@pytest.mark.requirement("applications:R9b")
@pytest.mark.asyncio
async def test_should_refuse_save_when_a_header_item_gap_names_no_fact(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = gapped_body()
    body["header"][2] = {"en": "Call {phone_number}"}
    created = await create_application(client, admin, body)

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["details"]["gaps"] == [
        {"place": "header[2]", "language": "en", "gap": "phone_number"}
    ]


@pytest.mark.requirement("applications:R9b")
@pytest.mark.asyncio
async def test_should_save_when_every_gap_names_a_fact_and_other_braces_are_literal(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = gapped_body()
    body["formFooter"] = {"en": "{Fair} play {  } {1st}", "mr": "{ venue }"}
    created = await create_application(client, admin, body)

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "saved"
    assert response.json()["layout"] is not None


# R15a: filled when printed -------------------------------------------------


@pytest.mark.requirement("applications:R15a")
@pytest.mark.asyncio
async def test_should_fill_every_gap_in_each_language_when_member_downloads_the_form(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_applicant(db_session, "ravi")
    published = await published_application(client, admin, gapped_body())

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form", headers=auth(member)
    )

    text = assert_pdf(response, published["id"], "ravi")
    assert "{" not in text and "}" not in text
    assert "I will attend the camp from 16–17 November 2026 at City Rink." in text
    assert "Venue: City Rink" in text
    assert "A fee of ₹1,50,000 is payable. Matches: 19–21 November 2026." in text
    assert "Rink-MR" in text  # the Marathi texts take the Marathi value


@pytest.mark.requirement("applications:R15a")
@pytest.mark.asyncio
async def test_should_fill_every_gap_when_admin_downloads_a_members_form(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_applicant(db_session, "ravi")
    published = await published_application(client, admin, gapped_body())

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form/ravi", headers=auth(admin)
    )

    text = assert_pdf(response, published["id"], "ravi")
    assert "I will attend the camp from 16–17 November 2026 at City Rink." in text
    assert "Rink-MR" in text
    assert "{venue}" not in text


@pytest.mark.requirement("applications:R15a")
@pytest.mark.asyncio
async def test_should_fill_the_preview_again_when_a_draft_fact_changes(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin, gapped_body())
    before = await preview(client, admin, created["id"])
    facts = {
        **gapped_body()["facts"],
        "camp": {
            "type": "date_range",
            "from": day(2026, 12, 1),
            "to": day(2026, 12, 3),
        },
        "venue": {"type": "name", "value": {"en": "Lake Arena", "mr": "Arena-MR"}},
    }

    patched = await client.patch(
        f"{APPLICATIONS}/{created['id']}", json={"facts": facts}, headers=auth(admin)
    )
    after = await preview(client, admin, created["id"])

    assert patched.status_code == 200, patched.text
    old = assert_pdf(before, created["id"], "demo.player")
    new = assert_pdf(after, created["id"], "demo.player")
    assert "I will attend the camp from 16–17 November 2026 at City Rink." in old
    assert "I will attend the camp from 1–3 December 2026 at Lake Arena." in new
    assert "Venue: Lake Arena" in new and "Venue: City Rink" not in new
    assert "Arena-MR" in new and "Rink-MR" not in new


@pytest.mark.requirement("applications:R15a")
@pytest.mark.asyncio
async def test_should_print_the_changed_fact_when_republished_after_an_edit(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_applicant(db_session, "ravi")
    published = await published_application(client, admin, gapped_body())
    for action in ("unpublish", "revert"):
        assert (await move(client, admin, published["id"], action)).status_code == 200
    facts = {**gapped_body()["facts"], "fee": {"type": "amount", "value": 2500}}
    patched = await client.patch(
        f"{APPLICATIONS}/{published['id']}", json={"facts": facts}, headers=auth(admin)
    )
    assert patched.status_code == 200, patched.text
    for action in ("save", "publish"):
        assert (await move(client, admin, published["id"], action)).status_code == 200

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form", headers=auth(member)
    )

    text = assert_pdf(response, published["id"], "ravi")
    assert "A fee of ₹2,500 is payable." in text
    assert "1,50,000" not in text


@pytest.mark.requirement("applications:R15a")
@pytest.mark.asyncio
async def test_should_print_a_gap_naming_no_fact_as_written_when_a_draft_is_previewed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = gapped_body()
    body["selections"][0]["en"] = "I will attend from {camp} with {kit}."
    created = await create_application(client, admin, body)

    response = await preview(client, admin, created["id"])

    text = assert_pdf(response, created["id"], "demo.player")
    assert "I will attend from 16–17 November 2026 with {kit}." in text


@pytest.mark.requirement("applications:R15a")
@pytest.mark.requirement("applications:R16")
@pytest.mark.asyncio
async def test_should_measure_the_filled_texts_when_the_layout_is_decided(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    sentence = {
        "en": "I will attend every practice session each morning of the camp week, "
        "on time and in full kit, and stay until the coach closes the session.",
        "mr": "मी शिबिराच्या आठवड्यात दररोज सकाळी प्रत्येक सराव सत्राला वेळेवर पूर्ण "
        "साहित्यासह उपस्थित राहीन आणि प्रशिक्षक सत्र संपवेपर्यंत थांबेन.",
    }
    short = template_body()
    short["facts"]["practice"] = {"type": "name", "value": {"en": "x", "mr": "x"}}
    short["selections"] = [{"en": "{practice}", "mr": "{practice}"}] * 4
    long = {**short, "facts": {**short["facts"]}}
    long["facts"]["practice"] = {"type": "name", "value": sentence}

    unfilled = await published_application(client, admin, short)
    filled = await published_application(client, admin, long)

    assert unfilled["layout"]["name"] == "one page"
    assert filled["layout"]["name"] == "signing part on page 2"


# R9a: the fill endpoint is gone ---------------------------------------------


@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
async def test_should_answer_not_found_when_a_clause_fill_is_asked_for(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await client.post(
        f"{APPLICATIONS}/clauses/fill",
        json={"clause": "declaration.trial", "facts": {}},
        headers=auth(admin),
    )

    assert response.status_code in (404, 405), response.text
