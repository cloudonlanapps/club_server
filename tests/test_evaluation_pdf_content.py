"""What the member copy PDF shows and how it is laid out (#535, R63, R64).

Read through the owner's preview, which renders the same document the
publish stores.
"""

import math

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    choice_item,
    create_evaluation_response,
    create_event_with_coach,
    create_general_evaluation,
    create_template,
    info_item,
    item_ids,
    mark_attendance,
    publish,
    put_answer,
    qa_item,
    rating_item,
)
from .evaluation_pdf_helpers import (
    PAGE_NUMBER,
    pdf_reader,
    pdf_text,
    preview,
    rename,
    sheet_texts,
)
from club_server.services.evaluation_pdf_text import date_text as date_label

from .helpers import create_admin_user, create_coach_user, create_member_user
from .media_helpers import (
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled", "clean_upload_dir")

# 5 January 2026 and 30 March 2026, 10:00 UTC.
JAN_5_2026_MS = 1_767_607_200_000
MAR_30_2026_MS = 1_774_864_800_000
A4_PORTRAIT_PT = (595.28, 841.89)


async def _draft(
    client: AsyncClient,
    db_session: AsyncSession,
    layout: list,
    *,
    name: str = "Spring review",
    **extra,
) -> tuple[str, str, int, list[int]]:
    """Admin and coach tokens, a draft of alice on `layout`, and its item ids."""
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    await create_member_user(db_session, "alice")
    template_id = await create_template(client, admin, name=name, layout=layout)
    ids = await item_ids(client, admin, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach, template_id, "alice", **extra
    )
    return admin, coach, evaluation_id, ids


async def _answer(client, coach, evaluation_id, item_id, **body) -> None:
    written = await put_answer(client, coach, evaluation_id, item_id, **body)
    assert written.status_code == 200, written.text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_write_non_latin_names_and_answers_in_the_embedded_fonts(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: text is Unicode in embedded Lato and Patrick Hand faces."""
    _, coach, evaluation_id, [rating, notes] = await _draft(
        client, db_session, [rating_item("Forward stride"), qa_item("Comments")]
    )
    await rename(db_session, "alice", "Łukasz", "Šimek")
    await _answer(client, coach, evaluation_id, rating, coachNote="Zoë – szybko")
    await _answer(client, coach, evaluation_id, notes, valueText="Świetnie, Ďakujem")

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Łukasz Šimek" in text
    assert "Zoë – szybko" in text
    assert "Świetnie, Ďakujem" in text
    assert b"/FontFile2" in response.content
    assert b"Lato" in response.content
    assert b"PatrickHand" in response.content


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_head_the_copy_with_title_name_and_review_period(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: the template's name, the member, and the period as "5 Jan 2026"."""
    _, coach, evaluation_id, _ = await _draft(
        client,
        db_session,
        [rating_item("Forward stride")],
        periodStartUtc=JAN_5_2026_MS,
        periodEndUtc=MAR_30_2026_MS,
    )

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Spring review" in text
    assert "Name:" in text and "Alice" in text
    assert "Review period:" in text
    assert "5 Jan 2026" in text and "30 Mar 2026" in text
    assert "QUESTION" in text and "RATING" in text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_leave_event_and_review_date_out_of_a_general_draft(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: no event line for a general review, no review date before publishing."""
    _, coach, evaluation_id, _ = await _draft(
        client, db_session, [rating_item("Forward stride")]
    )

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Event:" not in text
    assert "General" not in text
    assert "Review Date:" not in text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_head_a_published_event_review_with_its_event_and_review_date(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: the event's title, and the publication date as the review date."""
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session, "coach")
    await create_member_user(db_session, "alice")
    template_id = await create_template(
        client, admin, name="Camp report", layout=[rating_item("Edges")]
    )
    event_id, occurrence = await create_event_with_coach(
        client, admin, coach_names=["coach"], title="Autumn Skating Camp"
    )
    await mark_attendance(client, admin, event_id, occurrence, "alice")
    created = await create_evaluation_response(
        client, coach, template_id, "alice", eventId=event_id
    )
    assert created.status_code == 201, created.text
    evaluation_id = created.json()["id"]
    await publish(client, coach, evaluation_id)

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Event:" in text and "Autumn Skating Camp" in text
    assert "Review Date:" in text
    published = (
        await client.get(f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(coach))
    ).json()["publishedAtUtc"]
    assert date_label(published) in text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_write_each_kind_of_answer_as_the_member_reads_it(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: a pie with "value/max" for a range, stars without one, labels for
    levels, Yes / No and choices, numbers, and "Not answered"."""
    layout = [
        rating_item("Stars", rateType="stars"),
        rating_item("Range", rateMin=1, rateMax=10),
        {
            "type": "rating",
            "question": "Level",
            "rateValues": [
                {"value": 1, "text": "Learning"},
                {"value": 2, "text": "Solid"},
            ],
        },
        {"type": "yesNo", "question": "Ready?", "labelTrue": "Ready to move"},
        choice_item("Positions", ("forward", "defence"), multiple=True),
        {"type": "number", "question": "Goals"},
        rating_item("Untouched"),
    ]
    _, coach, evaluation_id, ids = await _draft(client, db_session, layout)
    stars, ranged, level, ready, positions, goals, _ = ids
    for item_id, body in (
        (stars, {"valueNum": 4}),
        (ranged, {"valueNum": 7}),
        (level, {"valueNum": 2}),
        (ready, {"valueNum": 1}),
        (positions, {"choices": ["forward", "defence"]}),
        (goals, {"valueNum": 12.5}),
    ):
        await _answer(client, coach, evaluation_id, item_id, **body)

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "7/10" in text
    assert "4/5" not in text
    for label in ("Solid", "Ready to move", "Forward, Defence", "12.5"):
        assert label in text
    assert text.count("Not answered") == 1


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_print_two_pages_side_by_side_on_each_a4_sheet(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: tall half-width pages, two to an A4 portrait sheet, numbered p / n.

    Sixty closing Q & A questions print as written blocks after the table
    (R64a), each with ruled lines to fill in: several pages' worth.
    """
    layout = [qa_item(f"Q{i}") for i in range(60)]
    _, coach, evaluation_id, _ = await _draft(client, db_session, layout)

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    sheets = pdf_reader(response.content).pages
    numbers = [
        [(int(p), int(n)) for p, n in PAGE_NUMBER.findall(text)]
        for text in sheet_texts(response.content)
    ]
    total = numbers[0][0][1]
    assert total >= 3
    assert len(sheets) == math.ceil(total / 2)
    assert numbers[0] == [(1, total), (2, total)]
    for sheet in sheets:
        assert (float(sheet.mediabox.width), float(sheet.mediabox.height)) == (
            pytest.approx(A4_PORTRAIT_PT[0], abs=0.1),
            pytest.approx(A4_PORTRAIT_PT[1], abs=0.1),
        )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_sign_the_copy_with_the_effective_owner_name(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: the signature line carries the owner's full name."""
    _, coach, evaluation_id, _ = await _draft(
        client, db_session, [rating_item("Forward stride")]
    )
    await rename(db_session, "coach", "Dana", "Pavlova")

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Dana Pavlova" in text
    assert "Signature" in text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_write_info_markdown_as_text_with_its_links(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: info markdown loses its markers, not the characters inside words."""
    _, coach, evaluation_id, _ = await _draft(
        client,
        db_session,
        [
            info_item(
                "Use **snake_case_names**; see [the guide](https://example.com/g)."
            ),
            rating_item("Forward stride"),
        ],
    )

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Use snake_case_names; see the guide." in text
    assert "**" not in text
    assert b"https://example.com/g" in response.content


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_show_the_club_logo_only_when_one_is_configured(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: the site's logo heads the copy; without one there is no image."""
    admin, coach, evaluation_id, _ = await _draft(
        client, db_session, [rating_item("Forward stride")]
    )
    without = await preview(client, coach, evaluation_id)
    logo = await upload(client, admin, access_roles=["public"])
    configured = await client.patch(
        "/v1/admin/preferences/site_media",
        json={"value": {"logo": logo["uuid"]}},
        headers=auth(admin),
    )
    assert configured.status_code == 200, configured.text

    with_logo = await preview(client, coach, evaluation_id)

    assert without.status_code == 200 and with_logo.status_code == 200
    assert b"/Subtype /Image" not in without.content
    assert b"/Subtype /Image" in with_logo.content


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R64a")
async def test_should_write_a_qa_answer_in_its_row_unless_it_closes_the_layout(
    client: AsyncClient, db_session: AsyncSession
):
    """R64a: an opening Q & A prints in the table, in order; a closing one after."""
    layout = [qa_item("Strengths"), rating_item("Stride"), qa_item("Plan")]
    _, coach, evaluation_id, [strengths, _, plan] = await _draft(
        client, db_session, layout
    )
    await _answer(client, coach, evaluation_id, strengths, valueText="Sharp edges")
    await _answer(client, coach, evaluation_id, plan, valueText="More crossovers")

    response = await preview(client, coach, evaluation_id)

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    order = [
        text.index(s)
        for s in ("Strengths", "Sharp edges", "Stride", "Plan", "More crossovers")
    ]
    assert order == sorted(order)
