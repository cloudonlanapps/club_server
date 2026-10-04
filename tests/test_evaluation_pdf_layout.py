"""Pure pieces of the member copy PDF (#535, R63): pagination, markdown,
answers, graphics and dates, tested without a document."""

import pytest

from club_server.schemas.evaluation import (
    EvaluationAnswerResponse,
    EvaluationEvidence,
    EvaluationMemberTemplate,
    EvaluationMemberView,
)
from club_server.schemas.evaluation_item import template_item_adapter
from club_server.schemas.evaluation_template import EvaluationLayoutSection
from club_server.services.evaluation_pdf_answer import answer_display
from club_server.services.evaluation_pdf_answer_kinds import (
    NotAnswered,
    PieAnswer,
    StarsAnswer,
    TextAnswer,
)
from club_server.services.evaluation_pdf_graphics import pie_svg, star_svg
from club_server.services.evaluation_pdf_markdown import markdown_runs
from club_server.services.evaluation_pdf_paginate import paginate
from club_server.services.evaluation_pdf_table import body_blocks
from club_server.services.evaluation_pdf_text import date_text

# 5 January 2026, 10:00 UTC.
JAN_5_2026_MS = 1_767_607_200_000


def _none(n: int) -> list[int | None]:
    """No sections."""
    return [None] * n


def _item(**fields):
    """A template item from its API fields."""
    return template_item_adapter.validate_python({"question": "Q", "id": 1, **fields})


def _answer(**fields) -> EvaluationAnswerResponse:
    return EvaluationAnswerResponse(item_id=1, **fields)


# Pagination -----------------------------------------------------------------


def test_should_fill_pages_up_to_the_capacity():
    assert paginate([40, 40, 40, 40], [False] * 4, _none(4), 100) == [
        [0, 1],
        [2, 3],
    ]


def test_should_move_a_kept_block_with_the_next_one():
    # Block 1 (a section bar) fits on page 1 but its row does not.
    assert paginate([50, 20, 50], [False, True, False], _none(3), 90) == [
        [0],
        [1, 2],
    ]


def test_should_give_an_oversized_block_its_own_page():
    assert paginate([30, 150, 30], [False] * 3, _none(3), 100) == [[0], [1], [2]]


@pytest.mark.requirement("evaluation:R63")
def test_should_move_a_section_whole_when_it_fits_on_a_fresh_page():
    """Section 1 (60 high) does not fit after block 0 (50), but fits alone."""
    assert paginate(
        [50, 20, 20, 20], [False, True, False, False], [None, 1, 1, 1], 100
    ) == [[0], [1, 2, 3]]


def test_should_let_sections_share_a_page_while_they_fit():
    assert paginate(
        [20, 20, 20, 20, 20],
        [True, False, True, False, False],
        [1, 1, 2, 2, None],
        100,
    ) == [[0, 1, 2, 3, 4]]


def test_should_break_a_section_taller_than_a_page_between_rows():
    assert paginate(
        [10, 20, 60, 60, 60],
        [False, True, False, False, False],
        [None, 1, 1, 1, 1],
        100,
    ) == [[0, 1, 2], [3], [4]]


# Where a written answer prints ----------------------------------------------


def _view(types: dict[int, str], layout: list) -> EvaluationMemberView:
    """A member view of items `types` (id -> type) laid out as `layout`."""
    items = [
        _item(
            id=item_id,
            type=type_,
            **({"rateMin": 1, "rateMax": 5} if type_ == "rating" else {}),
        )
        for item_id, type_ in types.items()
    ]
    return EvaluationMemberView(
        id=1,
        created_for="alice",
        created_by="coach",
        status="draft",
        template=EvaluationMemberTemplate(id=1, name="T", layout=layout, items=items),
        answers=[],
    )


def _table_parts(view: EvaluationMemberView) -> list[bool]:
    return [block.table_part for block in body_blocks(view, "https://club.test/v1")]


@pytest.mark.requirement("evaluation:R64a")
def test_should_print_only_the_closing_top_level_qa_run_after_the_table():
    """R64a: a Q & A before the end, or in a section, prints in its table row."""
    view = _view(
        {1: "qa", 2: "rating", 3: "qa", 4: "qa", 5: "qa"},
        [1, 2, EvaluationLayoutSection(section="S", items=[3]), 4, 5],
    )

    # The head, rows 1 and 2, the section bar and row 3; then 4 and 5 written.
    assert _table_parts(view) == [False, True, True, True, True, False, False]


@pytest.mark.requirement("evaluation:R64a")
def test_should_keep_every_qa_in_the_table_when_a_section_closes_the_layout():
    """R64a: a Q & A inside the closing section is not at the top level."""
    view = _view(
        {1: "qa", 2: "qa"},
        [1, EvaluationLayoutSection(section="S", items=[2])],
    )

    assert _table_parts(view) == [False, True, True, True]


@pytest.mark.requirement("evaluation:R64a")
def test_should_print_a_layout_of_only_qa_as_written_answers_with_no_table():
    """R64a: when every question is in the closing run there is no table."""
    view = _view({1: "qa", 2: "qa"}, [1, 2])

    assert _table_parts(view) == [False, False]


# Markdown -------------------------------------------------------------------


def _plain(markdown: str) -> str:
    return "".join(run.text for run in markdown_runs(markdown))


def test_should_strip_emphasis_markers_from_markdown():
    assert _plain("Skate **hard** and *fast*") == "Skate hard and fast"


def test_should_keep_underscores_and_asterisks_inside_words_and_sums():
    assert _plain("Use snake_case_names; 5 * 3 = 15") == (
        "Use snake_case_names; 5 * 3 = 15"
    )


def test_should_keep_a_link_text_and_its_target():
    runs = markdown_runs("See [the drills](https://example.com/d) today.")

    assert "".join(r.text for r in runs) == "See the drills today."
    [link] = [r for r in runs if r.link]
    assert (link.text, link.link) == ("the drills", "https://example.com/d")


def test_should_break_lines_between_paragraphs_and_decode_entities():
    assert _plain("First & foremost.\n\nSecond < third.") == (
        "First & foremost.\nSecond < third."
    )


def test_should_keep_non_latin_text_in_markdown():
    assert _plain("Łukasz Šimek – Zoë") == "Łukasz Šimek – Zoë"


# Answers --------------------------------------------------------------------


def test_should_draw_stars_for_a_star_rating():
    item = _item(type="rating", rateType="stars", rateMin=1, rateMax=5)

    assert answer_display(item, _answer(value_num=4)) == StarsAnswer(5, 4)


def test_should_draw_a_pie_for_a_range_rating():
    item = _item(type="rating", rateMin=1, rateMax=10)

    assert answer_display(item, _answer(value_num=7)) == PieAnswer(7, 10)


def test_should_write_the_level_label_for_labelled_levels():
    item = _item(
        type="rating",
        rateValues=[{"value": 1, "text": "Learning"}, {"value": 2, "text": "Solid"}],
    )

    assert answer_display(item, _answer(value_num=2)) == TextAnswer("Solid")


def test_should_write_the_yes_no_labels():
    plain = _item(type="yesNo")
    labelled = _item(type="yesNo", labelTrue="Ready", labelFalse="Not yet")

    assert answer_display(plain, _answer(value_num=1)) == TextAnswer("Yes")
    assert answer_display(plain, _answer(value_num=0)) == TextAnswer("No")
    assert answer_display(labelled, _answer(value_num=0)) == TextAnswer("Not yet")


def test_should_write_the_chosen_choice_labels():
    choices = [{"value": "f", "text": "Forward"}, {"value": "d", "text": "Defence"}]
    single = _item(type="singleChoice", choices=choices)
    multiple = _item(type="multipleChoice", choices=choices)

    assert answer_display(single, _answer(value_text="d")) == TextAnswer("Defence")
    assert answer_display(multiple, _answer(choices=["f", "d"])) == TextAnswer(
        "Forward, Defence"
    )


def test_should_write_numbers_without_a_needless_decimal():
    item = _item(type="number")

    assert answer_display(item, _answer(value_num=12.0)) == TextAnswer("12")
    assert answer_display(item, _answer(value_num=12.5)) == TextAnswer("12.5")


def test_should_say_not_answered_when_there_is_no_value():
    item = _item(type="rating", rateMin=1, rateMax=5)
    note_only = _answer(
        coach_note="Ask again",
        evidence=[EvaluationEvidence(media_uuid="u")],
    )

    assert answer_display(item, None) == NotAnswered()
    assert answer_display(item, note_only) == NotAnswered()


# Graphics -------------------------------------------------------------------


def test_should_fill_a_star_or_only_outline_it():
    filled = star_svg(filled=True, colour="#2f4b66")
    outlined = star_svg(filled=False, colour="#2f4b66")

    assert 'fill="#2f4b66"' in filled
    assert 'fill="none"' in outlined and 'stroke="#2f4b66"' in outlined


def test_should_draw_a_pie_arc_for_the_fraction():
    half = pie_svg(fraction=0.75, fill="#2f4b66", track="#d5dbe1", ring=0.13)
    empty = pie_svg(fraction=0, fill="#2f4b66", track="#d5dbe1", ring=0.13)
    full = pie_svg(fraction=1, fill="#2f4b66", track="#d5dbe1", ring=0.13)

    assert 'stroke="#d5dbe1"' in half and "<path" in half
    # Past half way round the arc takes the large sweep.
    assert " 0 1 1 " in half
    assert "<path" not in empty and 'stroke="#2f4b66"' not in empty
    assert full.count("<circle") == 2


# Dates ----------------------------------------------------------------------


def test_should_write_a_date_as_day_month_year():
    assert date_text(JAN_5_2026_MS) == "5 Jan 2026"
    assert date_text(None) == ""
