"""Application template shapes and the editor's catalogues (#93, R6–R8).

Print languages, printed texts, typed facts, the clause library and the
label catalogue.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .application_helpers import (
    APPLICATIONS,
    auth,
    create_applicant,
    create_application,
    day,
    get_application,
    template_body,
)
from .helpers import create_admin_user, create_coach_user, create_regular_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")


async def listed_ids(client: AsyncClient, token: str) -> list[int]:
    response = await client.get(APPLICATIONS, headers=auth(token))
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# R6, R6a ------------------------------------------------------------------


@pytest.mark.requirement("applications:R6")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "languages", [[], ["mr"], ["en", "mr", "hi"], ["en", "fr"], ["en", "en"]]
)
async def test_should_refuse_print_languages_when_not_english_and_one_other(
    client: AsyncClient, db_session: AsyncSession, languages: list[str]
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        APPLICATIONS, json={"languages": languages}, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert await listed_ids(client, admin) == []


@pytest.mark.requirement("applications:R6")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"title": {"fr": "Essai"}},  # not a catalogue language
        {"title": {}},
        {"title": {"en": ""}},
        {"declaration": {"clause": "declaration.trial", "en": "x"}},
        {"header": [{"fact": ""}]},
        {"terms": [{"heading": {"en": "Kit"}}]},
    ],
)
async def test_should_refuse_a_text_when_it_is_no_known_shape(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(APPLICATIONS, json=body, headers=auth(admin))

    assert response.status_code == 422, response.text
    assert await listed_ids(client, admin) == []


REFERENCES: list[dict[str, Any]] = [
    {"title": {"clause": "declaration.trial"}},
    {"declaration": {"clause": "declaration.trial"}},
    {"subtitle": {"fact": "camp"}},
    {"header": [{"fact": "venue"}]},
    {"header": [[{"clause": "header.matches", "args": {"dates": "matches"}}]]},
    {"selections": [{"clause": "selection.attend_training_camp"}]},
    {"formFooter": {"fact": "phone"}},
    {"terms": [{"clause": "rule.identity_proof"}]},
    {"terms": [{"heading": {"clause": "rule.fee"}, "body": {"en": "Fee"}}]},
    {"terms": [{"heading": {"en": "Fee"}, "body": {"fact": "fee"}}]},
    {"termsNotes": [{"clause": "note.follow_rules"}]},
    {"termsNotes": [{"text": {"clause": "note.keys_to_success"}}]},
    {"termsNotes": [{"text": {"en": "x"}, "attribution": {"fact": "venue"}}]},
]


@pytest.mark.requirement("applications:R6")
@pytest.mark.asyncio
@pytest.mark.parametrize("body", REFERENCES)
async def test_should_refuse_a_library_reference_when_a_template_is_created(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(APPLICATIONS, json=body, headers=auth(admin))

    assert response.status_code == 422, response.text
    assert await listed_ids(client, admin) == []


@pytest.mark.requirement("applications:R6")
@pytest.mark.asyncio
@pytest.mark.parametrize("change", REFERENCES)
async def test_should_refuse_a_library_reference_when_a_draft_is_edited(
    client: AsyncClient, db_session: AsyncSession, change: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    await db_session.commit()

    response = await client.patch(
        f"{APPLICATIONS}/{created['id']}", json=change, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    read = (await get_application(client, admin, created["id"])).json()
    assert read == created


@pytest.mark.requirement("applications:R6a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fact",
    [
        {"type": "date", "value": day(2026, 11, 1) + 3_600_000},  # not a midnight
        {"type": "date", "value": "2026-11-01"},
        {"type": "date_range", "from": day(2026, 11, 2), "to": day(2026, 11, 1)},
        {"type": "date_range", "from": day(2026, 11, 1)},
        {"type": "time", "value": "25:00"},
        {"type": "amount", "value": -5},
        {"type": "amount", "value": "500"},
        {"type": "name", "value": ""},
        {"type": "name", "value": {"mr": "पुणे"}},  # no English
        {"type": "choices", "value": []},
        {"type": "weekday", "value": "Monday"},
        {"type": "time", "value": "6:00", "from": day(2026, 11, 1)},
    ],
)
async def test_should_refuse_a_fact_when_its_shape_does_not_fit_its_type(
    client: AsyncClient, db_session: AsyncSession, fact: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        APPLICATIONS, json={"facts": {"f": fact}}, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    assert await listed_ids(client, admin) == []


@pytest.mark.requirement("applications:R6a")
@pytest.mark.asyncio
async def test_should_store_each_fact_type_when_shapes_fit(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    facts = {
        "day": {"type": "date", "value": day(2026, 11, 1)},
        "range": {
            "type": "date_range",
            "from": day(2026, 11, 1),
            "to": day(2026, 11, 1),
        },
        "start": {"type": "time", "value": "6:00"},
        "fee": {"type": "amount", "value": 150000},
        "venue": {"type": "name", "value": {"en": "City Rink", "mr": "सिटी रिंक"}},
        "kinds": {"type": "choices", "value": ["on_ice", "inline"]},
    }

    created = await create_application(client, admin, {"facts": facts})

    assert (await get_application(client, admin, created["id"])).json()[
        "facts"
    ] == facts


# R6b ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R6b")
@pytest.mark.asyncio
async def test_should_refuse_a_category_when_its_dates_are_reversed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin, {})
    reversed_dates = [
        {
            "label": {"en": "U12"},
            "bornOnOrAfterUtc": day(2014, 12, 31),
            "bornOnOrBeforeUtc": day(2013, 1, 1),
        }
    ]

    posted = await client.post(
        APPLICATIONS, json={"ageCategories": reversed_dates}, headers=auth(admin)
    )
    patched = await client.patch(
        f"{APPLICATIONS}/{created['id']}",
        json={"ageCategories": reversed_dates},
        headers=auth(admin),
    )

    assert posted.status_code == 422, posted.text
    assert patched.status_code == 422, patched.text
    assert await listed_ids(client, admin) == [created["id"]]
    read = (await get_application(client, admin, created["id"])).json()
    assert read["ageCategories"] == []


@pytest.mark.requirement("applications:R6b")
@pytest.mark.asyncio
async def test_should_save_overlapping_categories_when_each_band_is_ordered(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
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
        {
            "label": {"en": "Open", "mr": "खुला"},
            "bornOnOrAfterUtc": day(2013, 1, 1),
            "bornOnOrBeforeUtc": day(2013, 1, 1),
        },
    ]
    created = await create_application(client, admin, body)

    response = await client.post(
        f"{APPLICATIONS}/{created['id']}/save", headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    read = (await get_application(client, admin, created["id"])).json()
    assert read["status"] == "saved"
    assert read["ageCategories"] == body["ageCategories"]


# R7, R8 -------------------------------------------------------------------


@pytest.mark.requirement("applications:R7")
@pytest.mark.asyncio
async def test_should_serve_the_clause_library_when_admin_asks(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session)

    response = await client.get(f"{APPLICATIONS}/clauses", headers=auth(admin))

    assert response.status_code == 200
    body = response.json()
    assert body["languages"] == ["en", "mr", "hi"]
    assert body["choices"]["on_ice"]["en"] == "on-ice"
    clauses = {c["key"]: c for c in body["clauses"]}
    assert all(c["source"] == "builtin" for c in body["clauses"])
    matches = clauses["rule.matches"]
    assert matches["kind"] == "rule"
    assert matches["gaps"] == {"dates": "date_range", "venue": "name"}
    assert set(matches["heading"]) == set(matches["text"]) == {"en", "mr", "hi"}
    assert clauses["declaration.trial"]["heading"] is None
    assert "_review" not in response.text and "_comment" not in response.text


@pytest.mark.requirement("applications:R8")
@pytest.mark.asyncio
async def test_should_serve_the_label_catalogue_when_admin_asks(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await client.get(f"{APPLICATIONS}/labels", headers=auth(admin))

    assert response.status_code == 200
    body = response.json()
    assert body["languages"] == ["en", "mr", "hi"]
    assert body["labels"]["field.full_name"] == {
        "en": "Full name",
        "mr": "पूर्ण नाव",
        "hi": "पूरा नाम",
    }
    assert "_review" not in response.text


@pytest.mark.requirement("applications:R7")
@pytest.mark.requirement("applications:R8")
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/labels", "/clauses"])
@pytest.mark.parametrize("role", ["coach", "member"])
async def test_should_refuse_the_catalogue_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, path: str, role: str
):
    token = (
        await create_coach_user(db_session)
        if role == "coach"
        else await create_applicant(db_session, "ravi")
    )

    response = await client.get(f"{APPLICATIONS}{path}", headers=auth(token))

    assert response.status_code == 403


@pytest.mark.requirement("applications:R8")
@pytest.mark.asyncio
async def test_should_refuse_a_label_override_when_label_is_unknown(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post(
        APPLICATIONS,
        json={"labelOverrides": {"field.shoe_size": {"en": "Shoe size"}}},
        headers=auth(admin),
    )

    assert response.status_code == 422
    assert await listed_ids(client, admin) == []
