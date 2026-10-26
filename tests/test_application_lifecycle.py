"""Saving and moving application templates (#93, R9, R9a, R12–R14, R17)."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.application import Application
from club_server.services.application_catalogue import fingerprint

from .application_helpers import (
    APPLICATIONS,
    auth,
    create_applicant,
    create_application,
    get_application,
    move,
    published_application,
    template_body,
)
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
    assert layout["spread"]["withoutGuardian"] > layout["spread"]["withGuardian"] >= 0
    assert set(layout) == {"name", "spread"}  # the fingerprint stays inside

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
        "missing": ["formFooter", "terms[3].body", "labelOverrides.field.phone"],
        "language": "mr",
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
    assert response.json()["detail"]["details"]["missing"] == ["title", "declaration"]
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
    assert detail["details"]["missing"] == ["formFooter"]
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
            "declaration": {"clause": "declaration.camp"},
            "formFooter": {"en": "Play fair."},
            "header": [{"en": "City Rink"}],
        },
    )

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 200, response.text
    assert await status_of(client, admin, created["id"]) == "saved"


@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"declaration": {"clause": "declaration.unknown"}},
            "no clause 'declaration.unknown'",
        ),
        ({"subtitle": {"fact": "season"}}, "no fact named 'season'"),
        (
            {"selections": [{"clause": "selection.attend_training_camp"}]},
            "needs facts for dates",
        ),
        (
            {
                "selections": [
                    {
                        "clause": "selection.attend_training_camp",
                        "args": {"dates": "phone"},
                    }
                ]
            },
            "fact 'phone' is a name, the clause needs a date_range",
        ),
        (
            {
                "facts": {"k": {"type": "choices", "value": ["skis"]}},
                "subtitle": {"fact": "k"},
            },
            "no choice 'skis'",
        ),
    ],
)
async def test_should_refuse_save_when_a_reference_is_broken(
    client: AsyncClient, db_session: AsyncSession, change: dict[str, Any], reason: str
):
    admin = await create_admin_user(db_session)
    body = template_body()
    if "facts" in change:
        body["facts"].update(change.pop("facts"))
    body.update(change)
    created = await create_application(client, admin, body)
    await db_session.commit()

    response = await move(client, admin, created["id"], "save")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "APPLICATION_INVALID_REFERENCE"
    assert reason in response.json()["detail"]["message"]
    assert await status_of(client, admin, created["id"]) == "draft"


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
async def test_should_measure_again_when_the_stored_layout_is_out_of_date(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_applicant(db_session, "ravi")
    published = await published_application(client, admin)
    row = await db_session.get(Application, published["id"])
    assert row is not None and row.layout is not None
    assert row.layout["fingerprint"] == fingerprint()
    row.layout = {
        "name": "signing part on page 2",
        "spread": {"with_guardian": 0, "without_guardian": 0},
        "fingerprint": "an older catalogue",
    }
    await db_session.commit()

    response = await client.get(
        f"{APPLICATIONS}/{published['id']}/form/ravi", headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    layout = (await get_application(client, admin, published["id"])).json()["layout"]
    assert layout == published["layout"]
    db_session.expire_all()
    row = await db_session.get(Application, published["id"])
    assert row is not None and row.layout is not None
    assert row.layout["fingerprint"] == fingerprint()
