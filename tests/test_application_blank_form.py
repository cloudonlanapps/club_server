"""An admin's blank form, for filling in by hand (#129, applications R27).

A published template's blank form is its stored drawing (R25), carrying
its publish mark; a saved one is drawn on request from its stored layout,
with none. A draft has no layout and is refused. Neither carries a
watermark or any member's value.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.application_drawing import ApplicationDrawing
from club_server.services import application_drawing

from .application_helpers import (
    APPLICATIONS,
    assert_pdf,
    auth,
    create_applicant,
    create_application,
    move,
    published_application,
)
from .helpers import create_admin_user, create_coach_user
from .test_application_drawing import count_blank_draws

pytestmark = pytest.mark.usefixtures("applications_enabled")

GUARDIAN_NOTE = "Only for applicants below 18 years of age"
WATERMARK = "PREVIEW"
BLANK = "blank"  # the file name's last part: application-<id>-blank.pdf


def blank_form(application_id: int) -> str:
    return f"{APPLICATIONS}/{application_id}/blank-form"


def assert_blank(text: str):
    """Every label and box, and no member, demo or day in it."""
    assert "Selection Trial — Registration Form" in text
    assert "Parent / guardian contact" in text
    assert GUARDIAN_NOTE in text
    assert "Parent / guardian signature" in text
    assert WATERMARK not in text
    for value in ("Ravi", "Arjun", "downloaded", "demo.player"):
        assert value not in text


@pytest.mark.requirement("applications:R27")
@pytest.mark.asyncio
async def test_should_return_the_stored_drawing_when_the_template_is_published(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    await db_session.commit()
    draws = count_blank_draws(monkeypatch)

    response = await client.get(blank_form(published["id"]), headers=auth(admin))
    stored = (
        await db_session.execute(
            select(ApplicationDrawing).where(
                ApplicationDrawing.application_id == published["id"]
            )
        )
    ).scalar_one()

    text = assert_pdf(response, published["id"], BLANK)
    assert response.content == stored.pdf
    assert draws == []  # the drawing made on publish, not drawn again
    assert f"Form {published['publishId']}" in text
    assert_blank(text)


@pytest.mark.requirement("applications:R27")
@pytest.mark.asyncio
async def test_should_draw_the_blank_form_from_the_stored_layout_when_saved(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    assert (await move(client, admin, created["id"], "save")).status_code == 200
    seen: list[object] = []
    real = application_drawing.draw_blank

    def recorded(*args: object, **kwargs: object):
        seen.append(args[4])  # the stored layout
        return real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(application_drawing, "draw_blank", recorded)

    response = await client.get(blank_form(created["id"]), headers=auth(admin))

    text = assert_pdf(response, created["id"], BLANK)
    assert len(seen) == 1 and seen[0] is not None
    assert "Form " not in text  # no publish mark before a publish
    assert_blank(text)
    rows = (
        await db_session.execute(
            select(ApplicationDrawing).where(
                ApplicationDrawing.application_id == created["id"]
            )
        )
    ).all()
    assert rows == []  # a saved template's blank form is not stored


@pytest.mark.requirement("applications:R27")
@pytest.mark.asyncio
async def test_should_refuse_the_blank_form_when_the_template_is_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)

    response = await client.get(blank_form(created["id"]), headers=auth(admin))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "APPLICATION_NOT_SAVED"


@pytest.mark.requirement("applications:R27")
@pytest.mark.asyncio
async def test_should_refuse_the_blank_form_when_the_template_is_unknown(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await client.get(blank_form(9999), headers=auth(admin))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "APPLICATION_NOT_FOUND"


@pytest.mark.requirement("applications:R27")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member", "anonymous"])
async def test_should_refuse_the_blank_form_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    headers = {}
    if role == "coach":
        headers = auth(await create_coach_user(db_session))
    if role == "member":
        headers = auth(await create_applicant(db_session, "ravi"))
    await db_session.commit()

    response = await client.get(blank_form(published["id"]), headers=headers)

    assert response.status_code == (401 if role == "anonymous" else 403)
