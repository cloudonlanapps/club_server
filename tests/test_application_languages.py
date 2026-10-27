"""The club's languages drive every application translation (#133, applications R28–R31).

The profile options' languages, each a code and a name with English always
in, are the languages a template prints in and the library and labels are
written in. A language added shows blank translations; a language removed
hides its translations but keeps them, so adding it back restores them.
"""

from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.profile_languages import (
    coded_profile_options,
    migrate_profile_languages,
)
from club_server.services.application_catalogue import library
from club_server.services.application_text import fmt_day, fmt_time

from .application_helpers import APPLICATIONS, auth, template_body
from .extended_profile_helpers import ADMIN_PROFILE_OPTIONS, PROFILE_OPTIONS
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

LIBRARY = f"{APPLICATIONS}/library"
EN = {"code": "en", "name": "English"}
MR = {"code": "mr", "name": "Marathi"}
HI = {"code": "hi", "name": "Hindi"}
GU = {"code": "gu", "name": "Gujarati"}


async def set_languages(
    client: AsyncClient,
    admin: str,
    languages: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """The super admin's write of the club's languages, no positions."""
    from club_server.config import settings

    monkeypatch.setattr(settings, "extended_profile_enabled", True)
    return await client.patch(
        ADMIN_PROFILE_OPTIONS,
        json={"value": {"positions": [], "languages": languages}},
        headers=auth(admin),
    )


async def options(client: AsyncClient, token: str) -> dict[str, Any]:
    response = await client.get(PROFILE_OPTIONS, headers=auth(token))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# R28: the club's languages -------------------------------------------------


@pytest.mark.requirement("applications:R28")
@pytest.mark.asyncio
async def test_should_offer_english_and_marathi_when_languages_were_never_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    assert (await options(client, admin))["languages"] == [EN, MR]
    labels = await client.get(f"{APPLICATIONS}/labels", headers=auth(admin))
    clauses = await client.get(f"{APPLICATIONS}/clauses", headers=auth(admin))
    assert labels.json()["languages"] == ["en", "mr"]
    assert clauses.json()["languages"] == ["en", "mr"]


@pytest.mark.requirement("applications:R28")
@pytest.mark.asyncio
async def test_should_store_languages_with_codes_when_names_are_written(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)

    written = await set_languages(client, admin, [EN, "Marathi", "Hindi"], monkeypatch)

    assert written.status_code == 200, written.text
    assert written.json()["value"]["languages"] == [EN, MR, HI]
    assert (await options(client, admin))["languages"] == [EN, MR, HI]


@pytest.mark.requirement("applications:R28")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "languages",
    [
        [MR],  # no English
        [EN, MR, {"code": "mr", "name": "Marathi again"}],
        [EN, {"code": "Marathi", "name": "Marathi"}],  # not a code
        [EN, {"code": "mr", "name": ""}],
    ],
)
async def test_should_refuse_languages_when_they_do_not_fit(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    languages: list[Any],
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    written = await set_languages(client, admin, languages, monkeypatch)

    assert written.status_code == 422, written.text
    assert (await options(client, admin))["languages"] == [EN, MR]


@pytest.mark.requirement("applications:R28")
@pytest.mark.asyncio
async def test_should_refuse_a_position_label_in_a_language_the_club_lacks(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    from club_server.config import settings

    admin = await create_admin_user(db_session)
    monkeypatch.setattr(settings, "extended_profile_enabled", True)
    value = {
        "positions": [{"value": "Goalie", "label": {"en": "Goalie", "hi": "गोलकीपर"}}],
        "languages": [EN, MR],
    }

    written = await client.patch(
        ADMIN_PROFILE_OPTIONS, json={"value": value}, headers=auth(admin)
    )

    assert written.status_code == 422, written.text


@pytest.mark.requirement("applications:R28")
def test_should_give_every_stored_language_a_code_when_migrated():
    legacy = {
        "positions": [{"value": "Forward", "label": {"en": "Forward"}}],
        "languages": ["Marathi", "Hindi", "Konkani", "Klingon"],
    }

    migrated = coded_profile_options(legacy)

    assert migrated["positions"] == legacy["positions"]
    assert migrated["languages"] == [
        EN,  # English is always in
        MR,
        HI,
        {"code": "kok", "name": "Konkani"},
        {"code": "x-klingon", "name": "Klingon"},
    ]
    assert coded_profile_options(migrated) == migrated  # a second run: no change


@pytest.mark.requirement("applications:R28")
@pytest.mark.asyncio
async def test_should_read_stored_language_names_with_codes_after_the_migration(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await db_session.execute(
        text(
            "INSERT INTO system_preferences (key, value, updated_at, updated_by) "
            "VALUES ('profile_options', CAST(:value AS jsonb), 0, NULL)"
        ),
        {"value": '{"positions": [], "languages": ["English", "Marathi", "Hindi"]}'},
    )
    await db_session.commit()

    changed = await db_session.run_sync(
        lambda session: migrate_profile_languages(session.connection())
    )
    await db_session.commit()

    assert changed == 1
    assert (await options(client, admin))["languages"] == [EN, MR, HI]


# R29: templates print in the club's languages ------------------------------


@pytest.mark.requirement("applications:R29")
@pytest.mark.asyncio
async def test_should_refuse_a_print_language_the_club_does_not_have(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    refused = await client.post(
        APPLICATIONS, json={"languages": ["en", "hi"]}, headers=auth(admin)
    )
    _ = await set_languages(client, admin, [EN, MR, HI], monkeypatch)
    accepted = await client.post(
        APPLICATIONS, json={"languages": ["en", "hi"]}, headers=auth(admin)
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "APPLICATION_LANGUAGE_NOT_OFFERED"
    assert refused.json()["detail"]["details"]["languages"] == ["hi"]
    assert accepted.status_code == 201, accepted.text


@pytest.mark.requirement("applications:R29")
@pytest.mark.asyncio
async def test_should_refuse_to_save_when_a_print_language_was_removed(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)
    created = (
        await client.post(APPLICATIONS, json=template_body(), headers=auth(admin))
    ).json()
    _ = await set_languages(client, admin, [EN, HI], monkeypatch)

    saved = await client.post(
        f"{APPLICATIONS}/{created['id']}/save", headers=auth(admin)
    )

    assert saved.status_code == 422, saved.text
    assert saved.json()["detail"]["code"] == "APPLICATION_LANGUAGE_NOT_OFFERED"
    assert saved.json()["detail"]["details"]["languages"] == ["mr"]
    read = await client.get(f"{APPLICATIONS}/{created['id']}", headers=auth(admin))
    assert read.json()["status"] == "draft"


@pytest.mark.requirement("applications:R30")
@pytest.mark.asyncio
async def test_should_hide_and_keep_a_removed_language_in_a_template(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)
    body = template_body()
    created = (await client.post(APPLICATIONS, json=body, headers=auth(admin))).json()
    _ = await set_languages(client, admin, [EN, HI], monkeypatch)
    path = f"{APPLICATIONS}/{created['id']}"

    hidden = (await client.get(path, headers=auth(admin))).json()
    edited = await client.patch(
        path,
        json={
            "languages": ["en"],
            "title": {"en": "Trial form"},
            "terms": hidden["terms"],
        },
        headers=auth(admin),
    )
    _ = await set_languages(client, admin, [EN, MR], monkeypatch)
    restored = (await client.get(path, headers=auth(admin))).json()

    assert hidden["title"] == {"en": body["title"]["en"]}
    assert hidden["terms"][0]["heading"] == {"en": "Identity Proof Required"}
    assert hidden["ageCategories"][0]["label"] == {"en": "Under-12"}
    assert edited.status_code == 200, edited.text
    assert restored["title"] == {"en": "Trial form", "mr": body["title"]["mr"]}
    assert restored["terms"] == body["terms"]


# R30: library texts --------------------------------------------------------


@pytest.mark.requirement("applications:R30")
@pytest.mark.asyncio
async def test_should_hide_and_keep_a_removed_language_in_a_library_text(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)
    _ = await set_languages(client, admin, [EN, MR, HI], monkeypatch)
    texts = (await client.get(LIBRARY, headers=auth(admin))).json()
    matches = next(t for t in texts if t["key"] == "rule.matches")
    assert set(matches["text"]) == {"en", "mr", "hi"}  # seeded in every language
    _ = await set_languages(client, admin, [EN, MR], monkeypatch)
    path = f"{LIBRARY}/{matches['id']}"

    hidden = (await client.get(path, headers=auth(admin))).json()
    edited = await client.patch(
        path, json={"text": {"en": "Matches at {venue}."}}, headers=auth(admin)
    )
    _ = await set_languages(client, admin, [EN, MR, HI], monkeypatch)
    restored = (await client.get(path, headers=auth(admin))).json()

    assert set(hidden["text"]) == set(hidden["heading"]) == {"en", "mr"}
    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == {"en": "Matches at {venue}."}
    assert restored["text"] == {
        "en": "Matches at {venue}.",
        "hi": matches["text"]["hi"],
    }
    assert restored["heading"] == matches["heading"]


@pytest.mark.requirement("applications:R30")
@pytest.mark.asyncio
async def test_should_show_a_blank_translation_when_a_language_is_added(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)
    _ = await set_languages(client, admin, [EN, MR, GU], monkeypatch)

    texts = (await client.get(LIBRARY, headers=auth(admin))).json()
    clauses = (await client.get(f"{APPLICATIONS}/clauses", headers=auth(admin))).json()

    assert clauses["languages"] == ["en", "mr", "gu"]
    assert all("gu" not in t["text"] and "hi" not in t["text"] for t in texts)


# A language without the library's formats prints a fact as English does ----


@pytest.mark.requirement("applications:R31")
def test_should_print_a_fact_as_english_when_a_language_has_no_formats():
    lib = library()

    assert fmt_day(date(2026, 11, 19), "gu", lib) == "19 November 2026"
    assert fmt_time("18:30", "gu", lib) == "6:30 pm"
