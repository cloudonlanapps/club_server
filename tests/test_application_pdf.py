"""The form's pure parts, unit-tested: eligibility, fitting, preview member (#93).

These drive the ported PDF code directly with the form's own documents
(ISO dates, snake-case keys), the way the service hands them over.
"""

from datetime import date
from typing import Any

import pytest

from club_server.exceptions import (
    ApplicationDoesNotFitException,
    ApplicationNotEligibleException,
)
from club_server.services.application_catalogue import labels, library
from club_server.services.application_form import draw
from club_server.services.application_pdf_builder import ApplicationFormBuilder
from club_server.services.application_pdf_fit import WORST_CASE_MEMBER, check
from club_server.services.application_pdf_layout import FIT_SIZES, VALUE
from club_server.services.application_rules import (
    is_eligible,
    is_minor,
    matching_category,
    preview_member,
)
from club_server.services.application_text import resolve

from .application_helpers import page_count, page_texts, pdf_text

TODAY = date(2026, 10, 26)
OPTIONS: dict[str, Any] = {
    "positions": [
        {"value": "Forward", "label": {"en": "Forward", "mr": "फॉरवर्ड"}},
        {"value": "Goalie", "label": {"en": "Goalie", "mr": "गोलकीपर"}},
    ],
    "languages": ["English"],
}
CATEGORIES: list[dict[str, Any]] = [
    {"label": {"en": "Under-12"}, "born_on_or_after": "2014-01-01"},
    {
        "label": {"en": "Under-15 Girls"},
        "born_on_or_after": "2011-01-01",
        "genders": ["female"],
    },
    {"label": {"en": "Seniors"}, "born_on_or_before": "2008-12-31"},
]
SELECTION = {
    "en": "I will attend the practice sessions every morning of the camp week.",
    "mr": "मी शिबिराच्या आठवड्यात दररोज सकाळी सरावाला उपस्थित राहीन.",
}


def document(selections: int = 0, categories: int = 2, **extra: Any) -> dict[str, Any]:
    """A small resolved English + Marathi template."""
    doc: dict[str, Any] = {
        "languages": ["en", "mr"],
        "facts": {},
        "organisation": {"en": "EXAMPLE CLUB", "mr": "उदाहरण क्लब"},
        "title": {"en": "Trial", "mr": "चाचणी"},
        "declaration": {"clause": "declaration.trial"},
        "age_categories": [
            {
                "label": {"en": f"Group {i}", "mr": f"गट {i}"},
                "born_on_or_after": "2000-01-01",
            }
            for i in range(categories)
        ],
        "selections": [SELECTION] * selections,
    }
    doc.update(extra)
    return resolve(doc, library())


@pytest.mark.requirement("applications:R11")
@pytest.mark.parametrize(
    ("born", "gender", "expected"),
    [
        (date(2015, 3, 1), "male", 0),
        (date(2014, 1, 1), "male", 0),  # on the cutoff
        (date(2012, 6, 1), "female", 1),
        (date(2012, 6, 1), "male", None),  # Under-15 is for girls only
        (date(2008, 12, 31), "other", 2),
        (date(2010, 1, 1), "male", None),
        (None, "male", None),  # no date of birth: not eligible
    ],
)
def test_should_match_the_first_category_when_born_and_gender_fit(
    born: date | None, gender: str, expected: int | None
):
    assert matching_category(CATEGORIES, born, gender) == expected


@pytest.mark.requirement("applications:R11")
def test_should_be_eligible_when_template_has_no_categories():
    assert is_eligible([], {"date_of_birth": None, "gender": None})
    assert not is_eligible(CATEGORIES, {"date_of_birth": None, "gender": "male"})
    assert is_eligible(CATEGORIES, {"date_of_birth": "2015-02-02", "gender": "male"})


@pytest.mark.requirement("applications:R11")
def test_should_refuse_to_draw_when_member_is_not_eligible():
    member = {**WORST_CASE_MEMBER, "date_of_birth": "1990-01-01"}

    with pytest.raises(ApplicationNotEligibleException) as caught:
        _ = draw(document(), member, OPTIONS, None, TODAY, None)

    assert str(caught.value) == "not eligible for this application"


@pytest.mark.requirement("applications:R21")
@pytest.mark.parametrize(
    ("born", "guardian", "shown"),
    [
        ("2010-10-27", None, True),  # 15
        ("2008-10-26", None, False),  # 18 today
        ("2008-10-27", None, True),  # 18 tomorrow
        ("1990-01-01", {"name": "Asha"}, True),
        (None, None, False),
    ],
)
def test_should_print_the_guardian_block_when_minor_or_guardian_set(
    born: str | None, guardian: dict[str, str] | None, shown: bool
):
    member = {"username": "ravi", "date_of_birth": born, "guardian": guardian}
    b = ApplicationFormBuilder(
        document(categories=0), member, labels(), OPTIONS, None, TODAY
    )

    assert b.shows_guardian() is shown
    assert is_minor(date.fromisoformat(born) if born else None, TODAY) is (
        shown and guardian is None
    )


@pytest.mark.requirement("applications:R16")
@pytest.mark.parametrize(
    ("selections", "layout"),
    [
        (0, "one page"),
        (6, "one page, tighter spacing"),
        (8, "signing part on page 2"),
    ],
)
def test_should_choose_the_first_layout_that_fits_when_measured(
    selections: int, layout: str
):
    decided = check(document(selections), labels(), OPTIONS, TODAY)

    assert decided["name"] == layout
    spread = decided["spread"]
    # page 1 is spread to fill: more room without the guardian block
    assert spread["without_guardian"] > spread["with_guardian"] >= 0


@pytest.mark.requirement("applications:R17")
def test_should_refuse_a_template_when_details_alone_do_not_fit_page_one():
    with pytest.raises(ApplicationDoesNotFitException) as caught:
        _ = check(document(categories=90), labels(), OPTIONS, TODAY)

    assert caught.value.overflow > 0
    assert f"{caught.value.overflow} pt too long" in str(caught.value)


@pytest.mark.requirement("applications:R16")
def test_should_open_page_two_with_the_signing_part_when_split():
    decided = check(document(8), labels(), OPTIONS, TODAY)

    content = draw(document(8), WORST_CASE_MEMBER, OPTIONS, None, TODAY, decided)

    first, second = page_texts(content)
    assert "Declaration" not in first
    assert "Declaration" in second


@pytest.mark.requirement("applications:R18")
def test_should_shrink_then_cut_an_answer_when_it_overflows_its_lines():
    b = ApplicationFormBuilder(
        document(), WORST_CASE_MEMBER, labels(), OPTIONS, None, TODAY
    )
    b.pdf.add_page()

    short, size = b.fit("Ravi", 200, 1)
    long, small = b.fit("w " * 300, 200, 3)

    assert (short, size) == (["Ravi"], VALUE)
    assert len(long) == 3
    assert long[-1].endswith("…")
    assert small == FIT_SIZES[-1]


@pytest.mark.requirement("applications:R18")
def test_should_keep_the_layout_when_answers_are_long_or_blank():
    decided = check(document(), labels(), OPTIONS, TODAY)
    blank = {"username": "ravi", "date_of_birth": "2015-01-01", "gender": "male"}

    full = draw(document(), WORST_CASE_MEMBER, OPTIONS, None, TODAY, decided)
    empty = draw(document(), blank, OPTIONS, None, TODAY, decided)

    assert page_count(full) == page_count(empty) == 1


@pytest.mark.requirement("applications:R19")
def test_should_give_the_preview_member_a_birthday_inside_the_first_category():
    girls = {"age_categories": [CATEGORIES[1]]}
    seniors = {"age_categories": [CATEGORIES[2]]}

    boy = preview_member({"age_categories": CATEGORIES}, OPTIONS, TODAY)
    girl = preview_member(girls, OPTIONS, TODAY)
    senior = preview_member(seniors, OPTIONS, TODAY)
    anyone = preview_member({}, {"positions": []}, TODAY)

    assert (boy["username"], boy["date_of_birth"], boy["gender"]) == (
        "demo.player",
        "2014-06-15",
        "male",
    )
    assert boy["preferred_position"] == "Forward"
    assert boy["membership_date"] == "2025-10-26"
    assert (girl["gender"], girl["first_name"]) == ("female", "Ananya")
    assert senior["date_of_birth"] == "2008-03-10"
    assert anyone["date_of_birth"] == "2014-06-15"
    assert anyone["preferred_position"] is None
    assert "at a local rink" in boy["past_experience"]
    assert all(
        is_eligible(t["age_categories"], m)
        for t, m in ((girls, girl), (seniors, senior))
    )


@pytest.mark.requirement("applications:R19")
def test_should_watermark_the_preview_when_drawn():
    template = document()
    member = preview_member(template, OPTIONS, TODAY)

    text = pdf_text(draw(template, member, OPTIONS, None, TODAY, None, preview=True))

    assert "PREVIEW — not for submission" in text
    assert "Arjun Vikram Deshpande" in text
    assert "demo.player" in text
