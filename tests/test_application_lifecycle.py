"""Saving and moving application templates (#93, R9, R9a, R12–R14, R17)."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.application import Application
from club_server.services import application_catalogue
from club_server.services.application_catalogue import CLAUSES_FILE, library

from .application_helpers import (
    APPLICATIONS,
    auth,
    create_applicant,
    create_application,
    get_application,
    move,
    page_texts,
    pdf_text,
    published_application,
    template_body,
)
from .extended_profile_helpers import OPTIONS, write_options
from .helpers import create_admin_user, create_coach_user, create_regular_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")


async def status_of(client: AsyncClient, admin: str, application_id: int) -> str:
    response = await get_application(client, admin, application_id)
    assert response.status_code == 200, response.text
    return response.json()["status"]


# R12 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R12")
@pytest.mark.asyncio
async def test_should_walk_the_lifecycle_when_admin_moves_a_template(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session)
    created = await create_application(client, admin)

    for action, expected in (
        ("save", "saved"),
        ("publish", "published"),
        ("unpublish", "saved"),
        ("revert", "draft"),
    ):
        response = await move(client, admin, created["id"], action)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected
        assert await status_of(client, admin, created["id"]) == expected


@pytest.mark.requirement("applications:R12")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("moves", "action", "state"),
    [
        ([], "publish", "draft"),
        ([], "unpublish", "draft"),
        ([], "revert", "draft"),
        (["save"], "save", "saved"),
        (["save"], "unpublish", "saved"),
        (["save", "publish"], "publish", "published"),
        (["save", "publish"], "revert", "published"),
        (["save", "publish"], "save", "published"),
    ],
)
async def test_should_refuse_a_move_when_it_does_not_start_from_its_state(
    client: AsyncClient,
    db_session: AsyncSession,
    moves: list[str],
    action: str,
    state: str,
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    for earlier in moves:
        assert (await move(client, admin, created["id"], earlier)).status_code == 200

    response = await move(client, admin, created["id"], action)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_TRANSITION"
    assert await status_of(client, admin, created["id"]) == state


@pytest.mark.requirement("applications:R12")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member"])
@pytest.mark.parametrize("action", ["save", "publish", "unpublish", "revert"])
async def test_should_refuse_a_move_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str, action: str
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    token = (
        await create_coach_user(db_session)
        if role == "coach"
        else await create_applicant(db_session, "ravi")
    )
    await db_session.commit()

    response = await move(client, token, created["id"], action)

    assert response.status_code == 403
    assert await status_of(client, admin, created["id"]) == "draft"


@pytest.mark.requirement("applications:R12")
@pytest.mark.asyncio
async def test_should_answer_not_found_when_moving_an_unknown_template(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await move(client, admin, 999, "save")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "APPLICATION_NOT_FOUND"


# R13 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R13")
@pytest.mark.asyncio
async def test_should_store_the_layout_when_saved_and_drop_it_when_reverted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)

    saved = await move(client, admin, created["id"], "save")

    layout = (await get_application(client, admin, created["id"])).json()["layout"]
    assert saved.json()["layout"] == layout
    assert layout["name"] == "one page"
    # every form prints the guardian block: one spread, under both keys (#128)
    assert layout["spread"]["withoutGuardian"] == layout["spread"]["withGuardian"] >= 0
    assert set(layout) == {"name", "spread"}  # the frozen options stay inside

    assert (await move(client, admin, created["id"], "revert")).status_code == 200
    assert (await get_application(client, admin, created["id"])).json()[
        "layout"
    ] is None


# R9, R9a ------------------------------------------------------------------


@pytest.mark.requirement("applications:R9")
@pytest.mark.asyncio
async def test_should_refuse_save_when_a_text_lacks_a_print_language(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = template_body()
    body["formFooter"] = {"en": "Play fair"}
    body["terms"].append(
        {"heading": {"en": "Kit", "mr": "साहित्य"}, "body": {"en": "Helmet"}}
    )
    body["labelOverrides"] = {"field.phone": {"en": "Mobile"}}
    created = await create_application(client, admin, body)
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_INCOMPLETE"
    assert detail["details"] == {
        "missing": [
            {"field": "formFooter", "languages": ["mr"]},
            {"field": "terms[3].body", "languages": ["mr"]},
            {"field": "labelOverrides.field.phone", "languages": ["mr"]},
        ]
    }
    assert await status_of(client, admin, created["id"]) == "draft"


@pytest.mark.requirement("applications:R9")
@pytest.mark.asyncio
async def test_should_refuse_save_when_title_or_declaration_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(
        client, admin, {"languages": ["en"], "formFooter": {"en": "Play fair."}}
    )
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422
    assert response.json()["detail"]["details"]["missing"] == [
        {"field": "title", "languages": ["en"]},
        {"field": "declaration", "languages": ["en"]},
    ]
    assert await status_of(client, admin, created["id"]) == "draft"


@pytest.mark.requirement("applications:R9")
@pytest.mark.asyncio
async def test_should_refuse_save_when_form_footer_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = template_body()
    del body["formFooter"]
    created = await create_application(client, admin, body)
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_INCOMPLETE"
    assert detail["details"] == {
        "missing": [{"field": "formFooter", "languages": ["en", "mr"]}]
    }
    assert await status_of(client, admin, created["id"]) == "draft"


@pytest.mark.requirement("applications:R9")
@pytest.mark.asyncio
async def test_should_name_english_when_only_the_english_text_is_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = template_body()
    body["title"] = {"mr": "x"}
    created = await create_application(client, admin, body)
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_INCOMPLETE"
    assert detail["details"] == {"missing": [{"field": "title", "languages": ["en"]}]}
    assert await status_of(client, admin, created["id"]) == "draft"


@pytest.mark.requirement("applications:R9")
@pytest.mark.asyncio
async def test_should_name_each_texts_own_language_when_two_texts_lack_different_ones(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = template_body()
    body["title"] = {"mr": "x"}
    body["declaration"] = {"en": "I agree to the rules."}
    created = await create_application(client, admin, body)
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["details"] == {
        "missing": [
            {"field": "title", "languages": ["en"]},
            {"field": "declaration", "languages": ["mr"]},
        ]
    }
    assert await status_of(client, admin, created["id"]) == "draft"


@pytest.mark.requirement("applications:R9")
@pytest.mark.asyncio
async def test_should_save_an_english_only_template_when_its_texts_are_english(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(
        client,
        admin,
        {
            "title": {"en": "Season registration"},
            "declaration": {"en": "I agree to the rules of the camp."},
            "formFooter": {"en": "Play fair."},
            "header": [{"en": "City Rink"}],
        },
    )

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 200, response.text
    assert await status_of(client, admin, created["id"]) == "saved"


# R17 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R17")
@pytest.mark.asyncio
async def test_should_refuse_save_when_the_details_do_not_fit_page_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = template_body()
    body["ageCategories"] = [
        {"label": {"en": f"Group {i}", "mr": f"गट {i}"}} for i in range(90)
    ]
    created = await create_application(client, admin, body)
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_DOES_NOT_FIT"
    assert detail["details"]["overflowPt"] > 0
    assert await status_of(client, admin, created["id"]) == "draft"


# R14 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R14")
@pytest.mark.asyncio
async def test_should_draw_from_the_stored_layout_without_measuring_again(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_applicant(db_session, "ravi")
    created = await create_application(client, admin)
    assert (await move(client, admin, created["id"], "save")).status_code == 200
    row = await db_session.get(Application, created["id"])
    assert row is not None and row.layout is not None
    # the stored layout is what publishing draws the form with (#128)
    stored = {**row.layout, "name": "signing part on page 2"}
    row.layout = stored
    await db_session.commit()
    publish = await move(client, admin, created["id"], "publish")
    assert publish.status_code == 200, publish.text
    published = publish.json()

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form/ravi", headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    first, second = page_texts(response.content)[:2]
    assert "Declaration" not in first  # drawn as stored: signing part on page 2
    assert "Declaration" in second
    db_session.expire_all()
    row = await db_session.get(Application, published["id"])
    assert row is not None and row.layout == stored


@pytest.mark.requirement("applications:R14")
@pytest.mark.asyncio
async def test_should_freeze_the_profile_options_with_the_layout_when_saved(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await write_options(client, admin)
    created = await create_application(client, admin)

    saved = await move(client, admin, created["id"], "save")

    assert saved.status_code == 200, saved.text
    row = await db_session.get(Application, created["id"])
    assert row is not None and row.layout is not None
    assert row.layout["profile_options"] == OPTIONS
    assert set(saved.json()["layout"]) == {"name", "spread"}


@pytest.mark.requirement("applications:R14")
@pytest.mark.asyncio
async def test_should_keep_the_form_when_the_clause_library_changes(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    admin = await create_admin_user(db_session)
    _ = await create_applicant(db_session, "ravi")
    published = await published_application(client, admin)
    before = await client.get(
        f"{APPLICATIONS}/{published['id']}/form/ravi", headers=auth(admin)
    )
    assert before.status_code == 200, before.text
    reworded = json.loads(CLAUSES_FILE.read_text(encoding="utf-8"))
    reworded["clauses"]["rule.matches"]["text"]["en"] = "Matches: {dates}, {venue}."
    changed = tmp_path / "clauses.json"
    _ = changed.write_text(json.dumps(reworded), encoding="utf-8")
    monkeypatch.setattr(application_catalogue, "CLAUSES_FILE", changed)
    library.cache_clear()
    try:
        after = await client.get(
            f"{APPLICATIONS}/{published['id']}/form/ravi", headers=auth(admin)
        )
        read = (await get_application(client, admin, published["id"])).json()
    finally:
        monkeypatch.undo()
        library.cache_clear()

    assert after.status_code == 200, after.text
    assert pdf_text(after.content) == pdf_text(before.content)
    assert "Tournament matches will take place" in pdf_text(after.content)
    assert read["layout"] == published["layout"]
    assert read["terms"] == published["terms"]


@pytest.mark.requirement("applications:R14")
@pytest.mark.asyncio
async def test_should_keep_the_filled_facts_when_the_library_formats_change(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    admin = await create_admin_user(db_session)
    _ = await create_applicant(db_session, "ravi")
    body = template_body()
    body["subtitle"] = {"en": "Trial days {matches}", "mr": "{matches}"}
    published = await published_application(client, admin, body)
    reworded = json.loads(CLAUSES_FILE.read_text(encoding="utf-8"))
    reworded["formats"]["months"]["en"][10] = "Novembre"
    changed = tmp_path / "clauses.json"
    _ = changed.write_text(json.dumps(reworded), encoding="utf-8")
    monkeypatch.setattr(application_catalogue, "CLAUSES_FILE", changed)
    library.cache_clear()
    try:
        form = await client.get(
            f"{APPLICATIONS}/{published['id']}/form/ravi", headers=auth(admin)
        )
        draft = await create_application(client, admin, body)
        draft_preview = await client.get(
            f"{APPLICATIONS}/{draft['id']}/preview", headers=auth(admin)
        )
    finally:
        monkeypatch.undo()
        library.cache_clear()

    assert form.status_code == 200, form.text
    assert "Trial days 19–21 November 2026" in pdf_text(form.content)
    assert draft_preview.status_code == 200, draft_preview.text
    assert "Trial days 19–21 Novembre 2026" in pdf_text(draft_preview.content)


# R22, R24 -----------------------------------------------------------------


@pytest.mark.requirement("applications:R22")
@pytest.mark.asyncio
async def test_should_give_a_publish_id_only_while_published(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_applicant(db_session, "ravi")
    created = await create_application(client, admin)
    assert created["publishId"] is None

    saved = await move(client, admin, created["id"], "save")
    published = await move(client, admin, created["id"], "publish")

    assert saved.json()["publishId"] is None
    publish_id = published.json()["publishId"]
    assert str(uuid.UUID(publish_id)) == publish_id
    assert (await get_application(client, admin, created["id"])).json()[
        "publishId"
    ] == publish_id
    assert (await get_application(client, member, created["id"])).json()[
        "publishId"
    ] == publish_id
    listed = await client.get(APPLICATIONS, headers=auth(admin))
    assert [i["publishId"] for i in listed.json()["items"]] == [publish_id]
    unpublished = await move(client, admin, created["id"], "unpublish")
    assert unpublished.json()["publishId"] is None
    assert (await get_application(client, admin, created["id"])).json()[
        "publishId"
    ] is None


@pytest.mark.requirement("applications:R22")
@pytest.mark.asyncio
async def test_should_replace_the_publish_id_when_published_again(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    first = await published_application(client, admin)

    assert (await move(client, admin, first["id"], "unpublish")).status_code == 200
    again = await move(client, admin, first["id"], "publish")

    assert again.status_code == 200, again.text
    assert again.json()["publishId"] not in (None, first["publishId"])
    assert (await get_application(client, admin, first["id"])).json()[
        "publishId"
    ] == again.json()["publishId"]


async def application_audit(client: AsyncClient, admin: str) -> list[dict[str, Any]]:
    response = await client.get("/v1/audit_log?limit=100", headers=auth(admin))
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["rows"]
    return [r for r in reversed(rows) if r["action"].endswith("_application")]


@pytest.mark.requirement("applications:R24")
@pytest.mark.asyncio
async def test_should_audit_each_move_with_the_publish_ids(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    ids: list[str | None] = []
    for action in ("save", "publish", "unpublish", "publish", "unpublish", "revert"):
        response = await move(client, admin, created["id"], action)
        assert response.status_code == 200, response.text
        ids.append(response.json()["publishId"])

    rows = await application_audit(client, admin)

    assert [r["action"] for r in rows] == [
        "save_application",
        "publish_application",
        "unpublish_application",
        "publish_application",
        "unpublish_application",
        "revert_application",
    ]
    assert all(r["actor"]["username"] == "admin" for r in rows)
    assert all(r["details"]["applicationId"] == created["id"] for r in rows)
    first, second = ids[1], ids[3]
    assert first and second and first != second
    assert rows[1]["details"]["publishId"] == first
    assert rows[1]["details"]["previousPublishId"] is None
    assert rows[2]["details"]["publishId"] == first
    assert rows[3]["details"]["publishId"] == second
    assert rows[3]["details"]["previousPublishId"] == first
    assert rows[4]["details"]["publishId"] == second
