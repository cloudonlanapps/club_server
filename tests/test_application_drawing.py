"""A published form drawn once and filled per download (#128, R13, R21, R25, R26).

The pure tests compare a form filled over its stored drawing with the same
form drawn in full: the same text on every page and the same marks (each
value's lines, at the same place and size, and each tick). The API tests
cover where the drawing is stored, when it is drawn again, and that a
download leaves the server free to answer other requests.
"""

import asyncio
import io
import threading
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.application_drawing import ApplicationDrawing
from club_server.services import application_downloads, application_drawing
from club_server.services.application_catalogue import labels
from club_server.services.application_form import draw_blank, form_builder
from club_server.services.application_pdf_fill import fill, overlay
from club_server.services.application_pdf_fit import (
    WORST_CASE_LOGO,
    WORST_CASE_MEMBER,
    check,
)
from club_server.services.application_pdf_layout import BOTTOM

from .application_helpers import (
    APPLICATIONS,
    assert_pdf,
    auth,
    create_applicant,
    move,
    pdf_text,
    published_application,
)
from .helpers import create_admin_user
from .media_helpers import (
    clean_upload_dir as clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)
from .test_application_pdf import OPTIONS, TODAY, document

pytestmark = pytest.mark.usefixtures("applications_enabled")

GUARDIAN_NOTE = "Only for applicants below 18 years of age"
GUARDIAN_NOTE_MR = "फक्त 18 वर्षांखालील अर्जदारांसाठी"
PUBLISH_ID = "0b5e1f7c-6d1a-4c9e-8f0e-2a3b4c5d6e7f"
POSITIONS: dict[str, Any] = {
    "positions": [
        {"value": "Forward", "label": {"en": "Forward", "mr": "फॉरवर्ड"}},
        {"value": "Defence", "label": {"en": "Defence", "mr": "बचाव"}},
        {"value": "Goalie", "label": {"en": "Goalie", "mr": "गोलकीपर"}},
    ],
    "languages": ["English", "Marathi", "Hindi"],
}
MINOR: dict[str, Any] = {
    "username": "ravi",
    "first_name": "Ravi",
    "middle_name": "Kumar",
    "last_name": "Sharma",
    "date_of_birth": "2015-05-14",
    "gender": "male",
    "phone": "+91 98765 43210",
    "email": "ravi@example.org",
    "address": {
        "addr_line1": "Flat 4, Sai Residency",
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411038",
    },
    "preferred_positions": ["Forward"],
    "guardian": {"name": "Meera Sharma", "relation": "Mother", "phone": "+91 91234"},
    "chosen_category": 0,  # the document's groups overlap
}
ADULT: dict[str, Any] = {
    "username": "asha",
    "first_name": "Asha",
    "last_name": "Kulkarni",
    "date_of_birth": "2001-03-01",
    "gender": "female",
    "chosen_category": 0,
}
MEMBERS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "a minor with a guardian": (MINOR, OPTIONS),
    "an adult without one": (ADULT, OPTIONS),
    "long answers, shrunk and cut": (
        {**WORST_CASE_MEMBER, "username": "ravi", "chosen_category": 0},
        OPTIONS,
    ),
    "a Marathi name": (
        {**MINOR, "first_name": "रवी", "middle_name": "", "last_name": "शर्मा"},
        OPTIONS,
    ),
    "several positions and languages": (
        {
            **MINOR,
            "preferred_positions": ["Forward", "Goalie"],
            "preferred_languages": ["Marathi", "English", "Hindi"],
            "club_district": "Pune",
            "past_experience": "Two seasons of inline skating.",
        },
        POSITIONS,
    ),
    "a chosen category": ({**MINOR, "chosen_category": 1}, OPTIONS),
}


def plain(content: bytes) -> str:
    """Every page's text without the control characters a Devanagari font
    subset leaves in it, which differ from one PDF to another."""
    return "".join(c for c in pdf_text(content) if c >= " ")


def devanagari(c: str) -> bool:
    return "\u0900" <= c <= "\u097f"


def words(content: bytes) -> list[str]:
    """Every word printed, in any order: a filled page writes its values
    after the drawing beneath, so only the order differs. A Devanagari word
    keeps its Devanagari letters only: extraction maps a glyph the font
    subset holds under another code to an arbitrary character, which
    differs from one PDF to another."""
    found: list[str] = []
    for word in plain(content).split():
        if any(devanagari(c) for c in word):
            word = "".join(c for c in word if devanagari(c))
        found.append(word)
    return sorted(found)


def published(selections: int = 1, options: dict[str, Any] = OPTIONS):
    """A template's document, its stored layout, drawing and places."""
    doc = document(selections=selections)
    stored = check(doc, labels(), options, TODAY)
    drawing, places = draw_blank(doc, options, None, TODAY, stored, PUBLISH_ID)
    return doc, stored, drawing, places


# R25: the fill matches the full drawing ------------------------------------


@pytest.mark.requirement("applications:R25")
@pytest.mark.parametrize("case", list(MEMBERS))
def test_should_match_the_full_drawing_when_a_stored_drawing_is_filled(case: str):
    member, options = MEMBERS[case]
    doc, stored, drawing, places = published(options=options)
    builder = form_builder(doc, member, options, None, TODAY, stored, False, PUBLISH_ID)
    full = bytes(builder.build().output())

    filled = fill(drawing, places, doc, member, options, TODAY)
    _, marks = overlay(places, doc, member, options, TODAY)

    assert sorted(marks) == sorted(builder.marks)
    assert any(m[0] == "tick" for m in marks)
    assert words(filled) == words(full)
    assert len(PdfReader(io.BytesIO(filled)).pages) == len(
        PdfReader(io.BytesIO(full)).pages
    )


@pytest.mark.requirement("applications:R25")
def test_should_write_no_member_data_when_the_drawing_is_stored():
    doc, _stored, drawing, places = published()

    text = pdf_text(drawing)

    assert "Sharma" not in text and "downloaded" not in text
    assert f"Form {PUBLISH_ID}" in text and "Page 1 of" in text
    assert set(places["fields"]) >= {
        "field.full_name",
        "field.address",
        "field.guardian_name",
    }
    assert set(places["choices"]) == {
        "field.gender",
        "field.age_category",
        "field.preferred_position",
    }
    assert places["sign_date"]["page"] == 1
    assert places["pages"] == len(PdfReader(io.BytesIO(drawing)).pages)
    assert doc["age_categories"]  # the template has a category row


@pytest.mark.requirement("applications:R25")
def test_should_print_the_footer_and_signature_day_when_filled():
    doc, _stored, drawing, places = published()

    text = pdf_text(fill(drawing, places, doc, MINOR, OPTIONS, TODAY))

    assert "ravi · downloaded 26 Oct 2026" in text
    assert "26 / 10 / 2026" in text
    assert f"Form {PUBLISH_ID}" in text


# R21: the guardian block on every form -------------------------------------


@pytest.mark.requirement("applications:R21")
@pytest.mark.parametrize("member", [MINOR, ADULT], ids=["minor", "adult"])
def test_should_print_the_guardian_block_and_its_note_on_every_form(
    member: dict[str, Any],
):
    doc, _stored, drawing, places = published()

    text = plain(fill(drawing, places, doc, member, OPTIONS, TODAY))

    assert "Parent / guardian contact" in text
    assert GUARDIAN_NOTE in text
    # extraction may split a Devanagari word where its glyphs join
    assert GUARDIAN_NOTE_MR.replace(" ", "") in text.replace(" ", "")
    assert "Parent / guardian signature" in text
    assert ("Meera Sharma" in text) is (member is MINOR)


@pytest.mark.requirement("applications:R21")
def test_should_word_the_guardian_note_in_every_catalogue_language():
    note = labels()["note.guardian"]

    assert set(note) == {"en", "mr", "hi"} and all(note.values())
    assert note["en"] == GUARDIAN_NOTE


# R13: one spread ----------------------------------------------------------


@pytest.mark.requirement("applications:R13")
def test_should_store_one_spread_under_both_keys_when_saved():
    decided = check(document(), labels(), OPTIONS, TODAY)

    spread = decided["spread"]
    assert spread["with_guardian"] == spread["without_guardian"] >= 0


@pytest.mark.requirement("applications:R14")
@pytest.mark.parametrize("selections", [0, 6, 8])
def test_should_fit_page_one_when_a_layout_saved_with_two_spreads_is_drawn(
    selections: int,
):
    # A layout saved before every form printed the guardian block: two
    # spreads, measured without its note.
    decided = check(document(selections), labels(), OPTIONS, TODAY)
    legacy = {
        "name": decided["name"],
        "spread": {
            "with_guardian": decided["spread"]["with_guardian"] + 0.6,
            "without_guardian": decided["spread"]["with_guardian"] + 5,
        },
    }
    member = {**MINOR, "chosen_category": 0}

    def page_one_end(stored: dict[str, Any]) -> tuple[str, float]:
        b = form_builder(
            document(selections),
            member,
            OPTIONS,
            WORST_CASE_LOGO,
            TODAY,
            stored,
            False,
            None,
        )
        _ = b.build()
        assert b.page1_end is not None
        return b.layout.name, b.page1_end

    name, end = page_one_end(legacy)

    # spread again with the note: as a layout saved now, inside the page
    assert (name, end) == (decided["name"], pytest.approx(page_one_end(decided)[1]))
    assert end <= BOTTOM


# R25: stored on publish, drawn again when stale ----------------------------


async def stored_drawing(db: AsyncSession, application_id: int) -> ApplicationDrawing:
    found = (
        await db.execute(
            select(ApplicationDrawing).where(
                ApplicationDrawing.application_id == application_id
            )
        )
    ).scalar_one_or_none()
    assert found is not None
    await db.refresh(found)
    return found


def count_blank_draws(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the blank drawings made from now on."""
    calls: list[int] = []
    real = application_drawing.draw_blank

    def counted(*args: Any, **kwargs: Any):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(application_drawing, "draw_blank", counted)
    return calls


@pytest.mark.requirement("applications:R25")
@pytest.mark.asyncio
async def test_should_store_the_drawing_on_publish_and_fill_it_per_download(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    draws = count_blank_draws(monkeypatch)
    admin = await create_admin_user(db_session)
    published_template = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi", guardian=MINOR["guardian"])
    await db_session.commit()

    first = await client.get(
        f"{APPLICATIONS}/{published_template['id']}/form", headers=auth(member)
    )
    second = await client.get(
        f"{APPLICATIONS}/{published_template['id']}/form/ravi", headers=auth(admin)
    )
    stored = await stored_drawing(db_session, published_template["id"])

    assert stored.publish_id == published_template["publishId"]
    assert stored.pdf.startswith(b"%PDF")
    assert stored.places["fields"]["field.full_name"]["page"] == 1
    assert len(draws) == 1  # on publish; neither download draws the form
    for response in (first, second):
        text = assert_pdf(response, published_template["id"], "ravi")
        assert "Ravi Kumar Sharma" in text and "Meera Sharma" in text
        assert GUARDIAN_NOTE in text


@pytest.mark.requirement("applications:R25")
@pytest.mark.asyncio
async def test_should_draw_the_form_again_when_the_template_is_republished(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published_template = await published_application(client, admin)
    application_id = published_template["id"]
    await db_session.commit()

    assert (await move(client, admin, application_id, "unpublish")).status_code == 200
    gone = (
        await db_session.execute(
            select(ApplicationDrawing).where(
                ApplicationDrawing.application_id == application_id
            )
        )
    ).scalar_one_or_none()
    again = await move(client, admin, application_id, "publish")
    stored = await stored_drawing(db_session, application_id)

    assert gone is None
    assert again.status_code == 200
    assert stored.publish_id == again.json()["publishId"]
    assert stored.publish_id != published_template["publishId"]
    assert f"Form {stored.publish_id}" in pdf_text(stored.pdf)


@pytest.mark.requirement("applications:R25")
@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_upload_dir")
async def test_should_draw_the_form_again_when_the_club_logo_changes(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    published_template = await published_application(client, admin)
    application_id = published_template["id"]
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()
    without = (await stored_drawing(db_session, application_id)).logo_key
    draws = count_blank_draws(monkeypatch)
    logo = await upload(client, admin, access_roles=["public"])
    configured = await client.patch(
        "/v1/admin/preferences/site_media",
        json={"value": {"logo": logo["uuid"]}},
        headers=auth(admin),
    )
    assert configured.status_code == 200, configured.text

    first = await client.get(
        f"{APPLICATIONS}/{application_id}/form", headers=auth(member)
    )
    second = await client.get(
        f"{APPLICATIONS}/{application_id}/form", headers=auth(member)
    )
    stored = await stored_drawing(db_session, application_id)

    assert without == ""
    assert stored.logo_key not in ("", without)
    assert len(draws) == 1  # once, for the new logo
    for response in (first, second):
        _ = assert_pdf(response, application_id, "ravi")
        assert b"/Subtype /Image" in response.content


@pytest.mark.requirement("applications:R25")
@pytest.mark.asyncio
async def test_should_draw_the_form_on_first_download_when_published_before_drawings(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published_template = await published_application(client, admin)
    application_id = published_template["id"]
    member = await create_applicant(db_session, "ravi")
    # published before drawings were stored: no row
    _ = await db_session.execute(
        delete(ApplicationDrawing).where(
            ApplicationDrawing.application_id == application_id
        )
    )
    await db_session.commit()

    response = await client.get(
        f"{APPLICATIONS}/{application_id}/form", headers=auth(member)
    )
    stored = await stored_drawing(db_session, application_id)

    assert "Ravi Kumar Sharma" in assert_pdf(response, application_id, "ravi")
    assert stored.publish_id == published_template["publishId"]


# R26: off the event loop --------------------------------------------------


@pytest.mark.requirement("applications:R26")
@pytest.mark.asyncio
@pytest.mark.parametrize("download", ["own", "member", "preview"])
async def test_should_answer_other_requests_while_a_form_is_drawn(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    download: str,
):
    admin = await create_admin_user(db_session)
    published_template = await published_application(client, admin)
    application_id = published_template["id"]
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()
    started, released = threading.Event(), threading.Event()
    waited: list[bool] = []

    def slow(real: Any):
        def drawing(*args: Any, **kwargs: Any):
            started.set()
            waited.append(released.wait(10))
            return real(*args, **kwargs)

        return drawing

    if download == "preview":
        monkeypatch.setattr(
            application_downloads, "draw", slow(application_downloads.draw)
        )
    else:
        monkeypatch.setattr(
            application_downloads, "fill", slow(application_downloads.fill)
        )
    path, token = {
        "own": (f"{APPLICATIONS}/{application_id}/form", member),
        "member": (f"{APPLICATIONS}/{application_id}/form/ravi", admin),
        "preview": (f"{APPLICATIONS}/{application_id}/preview", admin),
    }[download]

    async def meanwhile():
        assert await asyncio.to_thread(started.wait, 10)
        answer = await client.get("/health")  # while the form is drawn
        released.set()
        return answer

    form, other = await asyncio.gather(
        client.get(path, headers=auth(token)), meanwhile()
    )

    assert other.status_code == 200
    assert waited == [True]  # released by the other request, not timed out
    assert form.status_code == 200, form.text
    assert form.content.startswith(b"%PDF")


@pytest.mark.requirement("applications:R26")
@pytest.mark.asyncio
async def test_should_measure_and_draw_off_the_event_loop_when_saved_and_published(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    admin = await create_admin_user(db_session)
    loop_threads: list[bool] = []
    main = threading.get_ident()
    from club_server.services import application as application_module

    def watched(module: Any, name: str):
        real = getattr(module, name)

        def run(*args: Any, **kwargs: Any):
            loop_threads.append(threading.get_ident() == main)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, run)

    watched(application_module, "decide_layout")
    watched(application_drawing, "draw_blank")

    _ = await published_application(client, admin)

    assert loop_threads == [False, False]


@pytest.mark.requirement("applications:R25")
def test_should_keep_today_out_of_the_stored_drawing():
    doc, _stored, drawing, places = published()
    later = date(2026, 12, 1)

    text = pdf_text(fill(drawing, places, doc, MINOR, OPTIONS, later))

    assert "01 / 12 / 2026" in text and "26 / 10 / 2026" not in text
