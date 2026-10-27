"""Which logo heads the PDFs (#135, applications R25a, evaluation R63).

``club_logo()`` takes the uploaded Website media logo (purpose ``logo``)
first, then the email logo file at ``<STATIC_DIR>/<EMAIL_LOGO_PATH>``, and
otherwise none. A file that is missing or not an image logs a warning and
the next source is tried. A published form's stored drawing records the
logo it was drawn with, so a change in which logo applies redraws it.
"""

import hashlib
import io
import logging
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings
from club_server.db.models.media import Media
from club_server.mailer.config import email_settings
from club_server.services.evaluation_pdf_logo import club_logo
from club_server.services.media import resolve_media_file_path

from .application_helpers import (
    APPLICATIONS,
    assert_pdf,
    create_applicant,
    published_application,
)
from .evaluation_helpers import (
    create_general_evaluation,
    create_template,
    rating_item,
)
from .evaluation_pdf_helpers import preview
from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    auth,
    clean_upload_dir as clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)
from .test_application_drawing import count_blank_draws, stored_drawing

pytestmark = pytest.mark.usefixtures("clean_upload_dir")

LOGO_PATH = "images/club_logo.png"


def static_png() -> bytes:
    """A PNG unlike any upload in these tests."""
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A static directory of this test's own, with the email logo path set
    and no file there yet."""
    monkeypatch.setattr(settings, "static_dir", str(tmp_path))
    monkeypatch.setattr(email_settings, "email_logo_path", LOGO_PATH)
    return tmp_path


def put_static_logo(static_dir: Path, content: bytes) -> bytes:
    path = static_dir / LOGO_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(content)
    return content


async def upload_site_logo(client: AsyncClient, admin: str) -> dict[str, str]:
    logo = await upload(client, admin, access_roles=["public"])
    configured = await client.patch(
        "/v1/admin/preferences/site_media",
        json={"value": {"logo": logo["uuid"]}},
        headers=auth(admin),
    )
    assert configured.status_code == 200, configured.text
    return logo


# Which source: upload, then the static email logo, then none ----------------


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_use_the_uploaded_site_logo_over_the_email_logo_file(
    client: AsyncClient, db_session: AsyncSession, static_dir: Path
):
    static = put_static_logo(static_dir, static_png())
    admin = await create_admin_user(db_session)
    _ = await upload_site_logo(client, admin)

    logo = await club_logo(db_session)

    assert logo is not None
    assert logo != static
    with Image.open(io.BytesIO(logo)) as image:
        assert image.size == (1, 1)  # the upload, not the 4x3 static file


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_use_the_email_logo_file_when_no_site_logo_is_uploaded(
    db_session: AsyncSession, static_dir: Path
):
    static = put_static_logo(static_dir, static_png())

    assert await club_logo(db_session) == static


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_accept_a_leading_slash_in_the_email_logo_path(
    db_session: AsyncSession, static_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    static = put_static_logo(static_dir, static_png())
    monkeypatch.setattr(email_settings, "email_logo_path", f"/{LOGO_PATH}")

    assert await club_logo(db_session) == static


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_fall_through_to_the_email_logo_when_the_upload_has_no_file(
    client: AsyncClient,
    db_session: AsyncSession,
    static_dir: Path,
    caplog: pytest.LogCaptureFixture,
):
    static = put_static_logo(static_dir, static_png())
    admin = await create_admin_user(db_session)
    uploaded = await upload_site_logo(client, admin)
    media = (
        await db_session.execute(select(Media).where(Media.uuid == uploaded["uuid"]))
    ).scalar_one()
    resolve_media_file_path(media).unlink()

    with caplog.at_level(logging.WARNING):
        logo = await club_logo(db_session)

    assert logo == static
    assert any(uploaded["uuid"] in r.getMessage() for r in caplog.records)


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_have_no_logo_when_the_email_logo_path_is_unset(
    db_session: AsyncSession,
    static_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    _ = put_static_logo(static_dir, static_png())
    monkeypatch.setattr(email_settings, "email_logo_path", "")

    with caplog.at_level(logging.WARNING):
        logo = await club_logo(db_session)

    assert logo is None
    assert not caplog.records  # nothing configured is not a fault


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_have_no_logo_and_warn_when_the_email_logo_file_is_missing(
    db_session: AsyncSession, static_dir: Path, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.WARNING):
        logo = await club_logo(db_session)

    assert logo is None
    assert any(LOGO_PATH in r.getMessage() for r in caplog.records)


@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
async def test_should_have_no_logo_and_warn_when_the_email_logo_is_not_an_image(
    db_session: AsyncSession, static_dir: Path, caplog: pytest.LogCaptureFixture
):
    _ = put_static_logo(static_dir, b"not an image")

    with caplog.at_level(logging.WARNING):
        logo = await club_logo(db_session)

    assert logo is None
    assert any(LOGO_PATH in r.getMessage() for r in caplog.records)


# What the PDFs show ---------------------------------------------------------


@pytest.mark.requirement("applications:R25")
@pytest.mark.requirement("applications:R25a")
@pytest.mark.asyncio
@pytest.mark.usefixtures("applications_enabled")
async def test_should_draw_the_form_again_when_the_email_logo_file_appears(
    client: AsyncClient,
    db_session: AsyncSession,
    static_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_admin_user(db_session)
    published_template = await published_application(client, admin)
    application_id = published_template["id"]
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()
    without = (await stored_drawing(db_session, application_id)).logo_key
    draws = count_blank_draws(monkeypatch)
    static = put_static_logo(static_dir, static_png())

    first = await client.get(
        f"{APPLICATIONS}/{application_id}/form", headers=auth(member)
    )
    second = await client.get(
        f"{APPLICATIONS}/{application_id}/form", headers=auth(member)
    )
    stored = await stored_drawing(db_session, application_id)

    assert without == ""
    assert stored.logo_key == hashlib.sha256(static).hexdigest()
    assert len(draws) == 1  # once, for the logo now in use
    for response in (first, second):
        _ = assert_pdf(response, application_id, "ravi")
        assert b"/Subtype /Image" in response.content


@pytest.mark.requirement("evaluation:R63")
@pytest.mark.asyncio
@pytest.mark.usefixtures("evaluations_enabled")
async def test_should_head_the_evaluation_copy_with_the_email_logo_file(
    client: AsyncClient, db_session: AsyncSession, static_dir: Path
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    _ = await create_member_user(db_session, "alice")
    template_id = await create_template(
        client, admin, name="Spring review", layout=[rating_item("Forward stride")]
    )
    evaluation_id = await create_general_evaluation(client, coach, template_id, "alice")
    without = await preview(client, coach, evaluation_id)
    _ = put_static_logo(static_dir, static_png())

    with_logo = await preview(client, coach, evaluation_id)

    assert without.status_code == 200 and with_logo.status_code == 200
    assert b"/Subtype /Image" not in without.content
    assert b"/Subtype /Image" in with_logo.content
