"""The club's labels: the label catalogue seeded once, editable per language
(#133, applications R8, R8a, R9).

The form's fixed wording ships in ``labels.json``; on first use every label
is copied into the club's store, from then on the club's own. An admin edits
a label's text per club language. A template printing in a language some
label lacks is refused on save, as a text lacking it is; a saved template
keeps the labels it was measured with.
"""

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services.application_catalogue import labels
from club_server.services.application_seed import ensure_labels_seeded

from .application_helpers import (
    APPLICATIONS,
    auth,
    create_application,
    move,
    pdf_text,
    template_body,
)
from .helpers import create_admin_user, create_coach_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

LABELS = f"{APPLICATIONS}/labels"
EN = {"code": "en", "name": "English"}
MR = {"code": "mr", "name": "Marathi"}
HI = {"code": "hi", "name": "Hindi"}
GU = {"code": "gu", "name": "Gujarati"}
FULL_NAME = "field.full_name"


async def set_languages(
    client: AsyncClient,
    admin: str,
    languages: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from club_server.config import settings

    monkeypatch.setattr(settings, "extended_profile_enabled", True)
    written = await client.patch(
        "/v1/admin/preferences/profile_options",
        json={"value": {"positions": [], "languages": languages}},
        headers=auth(admin),
    )
    assert written.status_code == 200, written.text


async def read_labels(client: AsyncClient, token: str) -> dict[str, Any]:
    response = await client.get(LABELS, headers=auth(token))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def edit_label(
    client: AsyncClient, token: str, key: str, text: dict[str, str]
) -> Any:
    return await client.patch(
        f"{LABELS}/{key}", json={"text": text}, headers=auth(token)
    )


@pytest.mark.requirement("applications:R8")
@pytest.mark.asyncio
async def test_should_serve_every_label_in_the_club_languages(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    body = await read_labels(client, admin)

    assert body["languages"] == ["en", "mr"]
    assert set(body["labels"]) == set(labels())
    assert body["labels"][FULL_NAME] == {"en": "Full name", "mr": "पूर्ण नाव"}


@pytest.mark.requirement("applications:R8a")
@pytest.mark.asyncio
async def test_should_store_a_label_when_admin_edits_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    edited = await edit_label(
        client, admin, FULL_NAME, {"en": "Player's name", "mr": "खेळाडूचे नाव"}
    )
    await ensure_labels_seeded(db_session)  # a later start
    await db_session.commit()

    assert edited.status_code == 200, edited.text
    assert edited.json() == {
        "key": FULL_NAME,
        "text": {"en": "Player's name", "mr": "खेळाडूचे नाव"},
    }
    read = await read_labels(client, admin)
    assert read["labels"][FULL_NAME] == {"en": "Player's name", "mr": "खेळाडूचे नाव"}


@pytest.mark.requirement("applications:R8a")
@pytest.mark.requirement("applications:R30")
@pytest.mark.asyncio
async def test_should_keep_a_removed_language_of_a_label(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    _ = await read_labels(client, admin)  # seeded in en, mr and hi

    edited = await edit_label(client, admin, FULL_NAME, {"en": "Name"})
    await set_languages(client, admin, [EN, MR, HI], monkeypatch)
    restored = await read_labels(client, admin)

    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == {"en": "Name"}  # Marathi left blank
    assert restored["labels"][FULL_NAME] == {"en": "Name", "hi": "पूरा नाम"}


@pytest.mark.requirement("applications:R8a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "text", "status", "code"),
    [
        ("field.nothing", {"en": "x"}, 404, "APPLICATION_LABEL_NOT_FOUND"),
        (FULL_NAME, {"mr": "नाव"}, 422, None),  # no English
        (
            FULL_NAME,
            {"en": "Name", "hi": "नाम"},
            422,
            "APPLICATION_LANGUAGE_NOT_OFFERED",
        ),
        (FULL_NAME, {"en": ""}, 422, None),
    ],
)
async def test_should_refuse_a_label_edit_when_it_does_not_fit(
    client: AsyncClient,
    db_session: AsyncSession,
    key: str,
    text: dict[str, str],
    status: int,
    code: str | None,
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await edit_label(client, admin, key, text)

    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()["detail"]["code"] == code
    read = await read_labels(client, admin)
    assert read["labels"][FULL_NAME] == {"en": "Full name", "mr": "पूर्ण नाव"}


@pytest.mark.requirement("applications:R8a")
@pytest.mark.asyncio
async def test_should_refuse_a_label_edit_when_caller_is_a_coach(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    await db_session.commit()

    response = await edit_label(client, coach, FULL_NAME, {"en": "Name"})

    assert response.status_code == 403, response.text
    read = await read_labels(client, admin)
    assert read["labels"][FULL_NAME]["en"] == "Full name"


def in_gujarati() -> dict[str, Any]:
    """The test template with its Marathi texts as Gujarati ones."""
    body = json.loads(json.dumps(template_body()).replace('"mr":', '"gu":'))
    body["languages"] = ["en", "gu"]
    return body


@pytest.mark.requirement("applications:R9")
@pytest.mark.requirement("applications:R30")
@pytest.mark.asyncio
async def test_should_refuse_to_save_when_a_label_lacks_an_added_language(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    await set_languages(client, admin, [EN, MR, GU], monkeypatch)
    created = await create_application(client, admin, in_gujarati())

    refused = await move(client, admin, created["id"], "save")
    current = (await read_labels(client, admin))["labels"]
    for key, text in current.items():
        if key != "field.past_experience":  # the template overrides it
            response = await edit_label(client, admin, key, {**text, "gu": "ગુ"})
            assert response.status_code == 200, response.text
    saved = await move(client, admin, created["id"], "save")

    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "APPLICATION_INCOMPLETE"
    missing = {m["field"]: m["languages"] for m in detail["details"]["missing"]}
    assert missing[f"labels.{FULL_NAME}"] == ["gu"]
    assert "labels.field.past_experience" not in missing
    assert set(missing) == {f"labels.{k}" for k in labels()} - {
        "labels.field.past_experience"
    }
    assert saved.status_code == 200, saved.text


@pytest.mark.requirement("applications:R14")
@pytest.mark.requirement("applications:R8a")
@pytest.mark.asyncio
async def test_should_print_the_labels_a_template_was_saved_with(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    saved = await create_application(client, admin)
    assert (await move(client, admin, saved["id"], "save")).status_code == 200
    draft = await create_application(client, admin)

    edited = await edit_label(
        client, admin, FULL_NAME, {"en": "Player name here", "mr": "पूर्ण नाव"}
    )
    blank = await client.get(
        f"{APPLICATIONS}/{saved['id']}/blank-form", headers=auth(admin)
    )
    preview = await client.get(
        f"{APPLICATIONS}/{draft['id']}/preview", headers=auth(admin)
    )

    assert edited.status_code == 200, edited.text
    assert blank.status_code == 200, blank.text
    assert "Player name here" not in pdf_text(blank.content)
    assert "Full name" in pdf_text(blank.content)
    assert preview.status_code == 200, preview.text
    assert "Player name here" in pdf_text(preview.content)
