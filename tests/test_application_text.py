"""Clauses, facts and completeness, unit-tested on the pure code (#93, #107).

The form's own document shape (ISO dates, snake-case keys) is what these
functions read; the API's shape is turned into it by
``application_document``.
"""

from datetime import date
from typing import Any

import pytest

from club_server.exceptions import ApplicationReferenceException
from club_server.services.application_catalogue import (
    catalogue_languages,
    fingerprint,
    labels,
    library,
    strip_notes,
)
from club_server.services.application_text import (
    fact_text,
    fill_clause,
    fmt_amount,
    fmt_range,
    incomplete,
    printable,
)

LIB = library()


def fact(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


@pytest.mark.requirement("applications:R7")
@pytest.mark.parametrize(
    ("lang", "expected"),
    [("en", "19 November 2026"), ("mr", "19 नोव्हेंबर 2026"), ("hi", "19 नवंबर 2026")],
)
def test_should_print_a_date_in_each_language_when_formatted(lang: str, expected: str):
    assert fact_text(fact(type="date", value="2026-11-19"), lang, LIB) == expected


@pytest.mark.requirement("applications:R7")
@pytest.mark.parametrize(
    ("start", "end", "lang", "expected"),
    [
        (date(2026, 11, 1), date(2026, 11, 15), "en", "1–15 November 2026"),
        (date(2026, 11, 1), date(2026, 11, 15), "mr", "1 ते 15 नोव्हेंबर 2026"),
        (date(2026, 11, 1), date(2026, 11, 15), "hi", "1 से 15 नवंबर 2026"),
        (date(2026, 10, 30), date(2026, 11, 2), "en", "30 October – 2 November 2026"),
        (
            date(2026, 12, 30),
            date(2027, 1, 2),
            "en",
            "30 December 2026 – 2 January 2027",
        ),
        (date(2026, 12, 30), date(2027, 1, 2), "mr", "30 डिसेंबर 2026 ते 2 जानेवारी 2027"),
    ],
)
def test_should_print_a_range_in_each_language_when_formatted(
    start: date, end: date, lang: str, expected: str
):
    assert fmt_range(start, end, lang, LIB) == expected


@pytest.mark.requirement("applications:R7")
@pytest.mark.parametrize(
    ("value", "lang", "expected"),
    [
        ("6:00", "en", "6:00 am"),
        ("6:00", "mr", "सकाळी 6:00"),
        ("6:00", "hi", "सुबह 6:00"),
        ("14:30", "en", "2:30 pm"),
        ("14:30", "mr", "दुपारी 14:30"),
        ("18:15", "mr", "संध्याकाळी 18:15"),
        ("21:00", "hi", "रात 21:00"),
        ("0:05", "en", "12:05 am"),
        ("12:00", "en", "12:00 pm"),
    ],
)
def test_should_print_a_time_with_its_part_of_day_when_formatted(
    value: str, lang: str, expected: str
):
    assert fact_text(fact(type="time", value=value), lang, LIB) == expected


@pytest.mark.requirement("applications:R7")
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (500, "500"),
        (1500, "1,500"),
        (150000, "1,50,000"),
        (12345678, "1,23,45,678"),
    ],
)
def test_should_group_an_amount_the_indian_way_when_formatted(
    value: int, expected: str
):
    assert fmt_amount(value) == expected


@pytest.mark.requirement("applications:R7")
@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", "on-ice and inline"),
        ("mr", "ऑन-आईस व इनलाइन"),
        ("hi", "ऑन-आइस और इनलाइन"),
    ],
)
def test_should_join_choices_with_the_language_and_when_formatted(
    lang: str, expected: str
):
    assert (
        fact_text(fact(type="choices", value=["on_ice", "inline"]), lang, LIB)
        == expected
    )


@pytest.mark.requirement("applications:R7")
def test_should_print_a_name_as_typed_or_per_language_when_formatted():
    typed = fact(type="name", value="City Rink")
    per_language = fact(type="name", value={"en": "City Rink", "mr": "सिटी रिंक"})

    assert fact_text(typed, "mr", LIB) == "City Rink"
    assert fact_text(per_language, "mr", LIB) == "सिटी रिंक"
    assert fact_text(per_language, "hi", LIB) == "City Rink"  # falls back to English


def template(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"languages": ["en", "mr"], "facts": {}}
    base.update(kwargs)
    return base


DECLARATION = {"en": "I agree to the rules.", "mr": "मला नियम मान्य आहेत."}
MATCHES = {"type": "date_range", "from": "2026-11-19", "to": "2026-11-21"}
VENUE = {"type": "name", "value": {"en": "City Rink", "mr": "सिटी रिंक"}}


@pytest.mark.requirement("applications:R9a")
def test_should_fill_a_clause_from_facts_in_every_language_when_filled():
    filled = fill_clause("rule.matches", {"dates": MATCHES, "venue": VENUE}, LIB)

    assert filled["heading"] == LIB["clauses"]["rule.matches"]["heading"]
    assert filled["text"]["en"] == (
        "Tournament matches will take place from 19–21 November 2026 at City Rink."
    )
    assert (
        filled["text"]["mr"]
        == "19 ते 21 नोव्हेंबर 2026 दरम्यान सिटी रिंक येथे स्पर्धेचे सामने होतील."
    )
    assert set(filled["text"]) == {"en", "mr", "hi"}


@pytest.mark.requirement("applications:R9a")
@pytest.mark.parametrize(
    ("clause", "facts", "reason"),
    [
        ("declaration.nope", {}, "no clause 'declaration.nope'"),
        ("rule.matches", {"dates": MATCHES}, "needs facts for venue"),
        (
            "selection.attend_training_camp",
            {"dates": {"type": "date", "value": "2026-11-16"}},
            "gap 'dates' needs a date_range, not a date",
        ),
        ("declaration.trial", {"venue": VENUE}, "has no gap 'venue'"),
        (
            "selection.attend_practice",
            {"dates": MATCHES, "kinds": {"type": "choices", "value": ["skis"]}},
            "no choice 'skis'",
        ),
    ],
)
def test_should_refuse_a_fill_when_the_clause_or_a_fact_does_not_fit(
    clause: str, facts: dict[str, Any], reason: str
):
    with pytest.raises(ApplicationReferenceException) as caught:
        _ = fill_clause(clause, facts, LIB)

    assert reason in str(caught.value)


@pytest.mark.requirement("applications:R6")
def test_should_print_custom_text_as_written_and_number_english_headings():
    doc = printable(
        template(
            header=[{"en": "City Rink"}, [{"en": "Camp"}, {"en": "Matches"}]],
            terms=[
                {"heading": {"en": "Kit", "mr": "साहित्य"}, "body": {"en": "Helmet"}},
                {"heading": {"en": "Fees"}, "body": {"en": "None"}},
            ],
            terms_notes=[
                {"text": {"en": "Be on time"}, "attribution": {"en": "Coach"}}
            ],
            declaration=DECLARATION,
        )
    )

    assert doc["header"] == [{"en": "City Rink"}, {"en": "Camp\nMatches"}]
    assert [t["heading"] for t in doc["terms"]] == [
        {"en": "1. Kit", "mr": "साहित्य"},
        {"en": "2. Fees"},
    ]
    assert doc["terms_notes"] == [
        {"text": {"en": "Be on time"}, "attribution": {"en": "Coach"}}
    ]
    assert doc["declaration"] == DECLARATION


@pytest.mark.requirement("applications:R9")
def test_should_name_each_text_missing_a_language_when_checked():
    doc = printable(
        template(
            title={"en": "Trial"},
            declaration=DECLARATION,
            form_footer={"en": "Play fair"},
            selections=[{"en": "I will come", "mr": "मी येईन"}, {"en": "Only English"}],
            terms=[{"heading": {"en": "Kit"}, "body": {"en": "Helmet", "mr": "हेल्मेट"}}],
            terms_notes=[{"text": {"en": "Be on time"}}],
            header=[{"en": "City Rink"}],  # header items may stay English only
        )
    )

    assert incomplete(doc) == [
        {"field": "title", "languages": ["mr"]},
        {"field": "formFooter", "languages": ["mr"]},
        {"field": "terms[0].heading", "languages": ["mr"]},
        {"field": "termsNotes[0].text", "languages": ["mr"]},
        {"field": "selections[1]", "languages": ["mr"]},
    ]


@pytest.mark.requirement("applications:R9")
def test_should_name_a_missing_title_or_declaration_when_checked():
    doc = printable(template(form_footer={"en": "x", "mr": "य"}))

    assert incomplete(doc) == [
        {"field": "title", "languages": ["en", "mr"]},
        {"field": "declaration", "languages": ["en", "mr"]},
    ]


@pytest.mark.requirement("applications:R9")
def test_should_name_a_missing_form_footer_when_checked():
    doc = printable(template(title={"en": "x", "mr": "य"}, declaration=DECLARATION))

    assert incomplete(doc) == [{"field": "formFooter", "languages": ["en", "mr"]}]


@pytest.mark.requirement("applications:R9")
def test_should_find_nothing_missing_when_english_only_template_is_complete():
    doc = printable(
        {
            "languages": ["en"],
            "title": {"en": "Trial"},
            "declaration": {"en": "I agree."},
            "form_footer": {"en": "Play fair"},
        }
    )

    assert incomplete(doc) == []


@pytest.mark.requirement("applications:R8")
def test_should_ship_every_label_and_clause_in_every_catalogue_language():
    langs = catalogue_languages()

    assert langs == ["en", "mr", "hi"]
    assert all(all(entry.get(lang) for lang in langs) for entry in labels().values())
    for clause in LIB["clauses"].values():
        assert set(clause["text"]) == set(langs)
    assert not any(key.startswith("_") for key in labels())


@pytest.mark.requirement("applications:R14")
def test_should_strip_reviewer_notes_and_fingerprint_the_catalogue():
    assert strip_notes({"a": {"_review": ["hi"], "en": "x"}, "_comment": "y"}) == {
        "a": {"en": "x"}
    }
    assert len(fingerprint()) == 64
