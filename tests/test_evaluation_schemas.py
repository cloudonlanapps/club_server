"""Schema integrity for evaluations (#302, #535).

Required by tdd_rules.md §5: validation success and failure, JSON
round-trip, defaults, and required-field enforcement.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from club_server.schemas.evaluation import (
    EvaluationAnswerInput,
    EvaluationCreate,
    EvaluationTransferRequest,
    EvaluationUpdate,
)
from club_server.schemas.evaluation_item import (
    EvaluationChoiceItem,
    EvaluationRatingItem,
    template_item_adapter,
)
from club_server.schemas.evaluation_template import EvaluationTemplateCreate

VARIANTS: list[dict[str, Any]] = [
    {"type": "rating", "question": "Skating", "rateMin": 1, "rateMax": 10},
    {
        "type": "rating",
        "question": "Effort",
        "rateValues": [{"value": 1, "text": "Low"}, {"value": 2, "text": "High"}],
    },
    {
        "type": "yesNo",
        "question": "Ready?",
        "labelTrue": "Yes",
        "labelFalse": "Not yet",
    },
    {
        "type": "singleChoice",
        "question": "Position",
        "choices": [{"value": "f", "text": "Forward"}],
    },
    {
        "type": "multipleChoice",
        "question": "Strengths",
        "choices": [{"value": "s", "text": "Speed"}, {"value": "v", "text": "Vision"}],
    },
    {"type": "number", "question": "Goals"},
    {"type": "qa", "question": "Summary", "allowEvidence": True},
    {"type": "info", "markdown": "Read **this** first."},
]


@pytest.mark.requirement("evaluation:R12")
@pytest.mark.parametrize(
    "raw", VARIANTS, ids=[v["type"] + str(i) for i, v in enumerate(VARIANTS)]
)
def test_should_round_trip_each_item_variant_through_json(raw: dict[str, Any]):
    """R12: every variant validates, and survives a dump and a re-validation."""
    item = template_item_adapter.validate_python(raw)

    wire = template_item_adapter.dump_python(item, by_alias=True, mode="json")
    restored = template_item_adapter.validate_python(wire)

    assert restored == item
    assert wire["type"] == raw["type"]


def test_should_default_flags_to_false():
    """Defaults: not required, not private, no evidence, no comment area."""
    item = EvaluationRatingItem.model_validate({"type": "rating", "question": "Q"})

    assert (item.is_required, item.is_private, item.allow_evidence) == (
        False,
        False,
        False,
    )
    assert item.show_comment_area is False
    assert item.require_comment_for == []
    assert item.id is None
    assert item.origin_item_id is None


@pytest.mark.requirement("evaluation:R12")
@pytest.mark.parametrize(
    "raw",
    [
        {"type": "slider", "question": "Speed"},
        {"type": "qa", "question": "Q", "choices": []},
        {"type": "info", "markdown": "x", "question": "Q"},
        {"type": "rating"},
        {"type": "info"},
    ],
    ids=[
        "unknown_type",
        "foreign_property",
        "info_question",
        "no_question",
        "no_markdown",
    ],
)
def test_should_reject_a_malformed_item(raw: dict[str, Any]):
    """R12: an unknown type, a property its type lacks, or a missing field."""
    with pytest.raises(ValidationError):
        _ = template_item_adapter.validate_python(raw)


@pytest.mark.requirement("evaluation:R12a")
@pytest.mark.parametrize(
    "raw",
    [
        {"rateMin": 5, "rateMax": 5},
        {"rateMin": 1},
        {
            "rateMin": 1,
            "rateMax": 5,
            "rateValues": [{"value": 1, "text": "A"}],
        },
        {"rateValues": [{"value": 2, "text": "A"}, {"value": 1, "text": "B"}]},
        {"rateValues": []},
        {"rateType": "stars", "rateValues": [{"value": 1, "text": "A"}]},
        {"showCommentArea": True, "requireCommentFor": [9]},
        {"requireCommentFor": [1]},
    ],
    ids=[
        "unordered",
        "half_range",
        "range_and_levels",
        "levels_out_of_order",
        "no_levels",
        "stars_with_levels",
        "comment_for_impossible_value",
        "comment_for_without_area",
    ],
)
def test_should_reject_an_inconsistent_rating(raw: dict[str, Any]):
    """R12a: a range or levels, never both; ordered; notes only for possible values."""
    with pytest.raises(ValidationError):
        _ = EvaluationRatingItem.model_validate(
            {"type": "rating", "question": "Q", **raw}
        )


@pytest.mark.requirement("evaluation:R12a")
def test_should_accept_comment_requirement_on_a_possible_rating():
    """R12a: a note may be required for low values of the scale."""
    item = EvaluationRatingItem.model_validate(
        {
            "type": "rating",
            "question": "Q",
            "showCommentArea": True,
            "requireCommentFor": [1, 2],
        }
    )

    assert item.require_comment_for == [1, 2]


@pytest.mark.requirement("evaluation:R12a")
@pytest.mark.parametrize(
    "raw",
    [
        {"choices": []},
        {"choices": [{"value": "a", "text": "A"}, {"value": "a", "text": "B"}]},
        {
            "choices": [{"value": "a", "text": "A"}],
            "showCommentArea": True,
            "requireCommentFor": ["b"],
        },
    ],
    ids=["no_choices", "repeated_value", "comment_for_unknown_choice"],
)
def test_should_reject_an_inconsistent_choice(raw: dict[str, Any]):
    """R12a: at least one choice, unique values, notes only for real choices."""
    with pytest.raises(ValidationError):
        _ = EvaluationChoiceItem.model_validate(
            {"type": "singleChoice", "question": "Q", **raw}
        )


@pytest.mark.requirement("evaluation:R12a")
def test_should_reject_a_yes_no_comment_requirement_that_is_not_a_boolean():
    """R12a: a Yes / No note requirement names true or false."""
    with pytest.raises(ValidationError):
        _ = template_item_adapter.validate_python(
            {
                "type": "yesNo",
                "question": "Q",
                "showCommentArea": True,
                "requireCommentFor": ["maybe"],
            }
        )


@pytest.mark.requirement("evaluation:R12c")
def test_should_reject_a_nested_section_in_a_create_payload():
    """R12c: a section holds items, never another section."""
    with pytest.raises(ValidationError):
        _ = EvaluationTemplateCreate.model_validate(
            {
                "name": "T",
                "layout": [
                    {
                        "section": "A",
                        "items": [
                            {
                                "section": "B",
                                "items": [{"type": "qa", "question": "Q"}],
                            }
                        ],
                    }
                ],
            }
        )


@pytest.mark.requirement("evaluation:R49")
def test_should_reject_a_template_payload_with_no_question():
    """R49: info text alone asks nothing."""
    with pytest.raises(ValidationError):
        _ = EvaluationTemplateCreate.model_validate(
            {"name": "T", "layout": [{"type": "info", "markdown": "x"}]}
        )


def test_should_round_trip_template_payload_through_json():
    """A template payload with a section survives the camelCase wire form."""
    payload = EvaluationTemplateCreate.model_validate(
        {
            "name": "T",
            "layout": [
                {"type": "info", "markdown": "Intro"},
                {"section": "Skating", "items": [VARIANTS[0]]},
            ],
        }
    )

    wire = payload.model_dump(by_alias=True, mode="json")
    restored = EvaluationTemplateCreate.model_validate(wire)

    assert restored == payload


def test_should_round_trip_create_payload_through_camel_case_aliases():
    """The wire form is camelCase; validating it back yields the same model."""
    payload = EvaluationCreate(
        template_id=1,
        created_for="alice",
        event_id=7,
        period_start_utc=100,
        period_end_utc=200,
    )

    wire = payload.model_dump(by_alias=True)
    restored = EvaluationCreate.model_validate(wire)

    assert "createdFor" in wire
    assert restored == payload


def test_should_default_optional_create_fields_to_none():
    """Everything but the template and the member is optional: general, no period."""
    payload = EvaluationCreate(template_id=1, created_for="alice")

    assert payload.event_id is None
    assert payload.period_start_utc is None
    assert payload.period_end_utc is None


@pytest.mark.parametrize(
    "raw",
    [
        {"templateId": 1},
        {"createdFor": "alice"},
        {"templateId": 1, "createdFor": "alice", "owner": "x"},
    ],
    ids=["no_member", "no_template", "owner"],
)
def test_should_reject_create_payload_missing_or_extra_fields(raw: dict[str, Any]):
    """R10a, R34: template and member are required; no owner may be named."""
    with pytest.raises(ValidationError):
        _ = EvaluationCreate.model_validate(raw)


@pytest.mark.requirement("evaluation:R4")
@pytest.mark.parametrize(
    "period",
    [
        {"periodStartUtc": 1},
        {"periodEndUtc": 1},
        {"periodStartUtc": 2, "periodEndUtc": 1},
    ],
    ids=["start_only", "end_only", "inverted"],
)
def test_should_reject_a_malformed_period(period: dict[str, int]):
    """R4: both bounds or neither, end not before start — on create and update."""
    with pytest.raises(ValidationError):
        _ = EvaluationCreate.model_validate(
            {"templateId": 1, "createdFor": "alice", **period}
        )
    with pytest.raises(ValidationError):
        _ = EvaluationUpdate.model_validate(period)


@pytest.mark.requirement("evaluation:R4")
def test_should_accept_a_period_that_ends_where_it_starts():
    """R4: a one-day period may end where it starts, on create and update."""
    period = {"periodStartUtc": 1_000, "periodEndUtc": 1_000}

    created = EvaluationCreate.model_validate(
        {"templateId": 1, "createdFor": "alice", **period}
    )
    updated = EvaluationUpdate.model_validate(period)

    assert (created.period_start_utc, created.period_end_utc) == (1_000, 1_000)
    assert (updated.period_start_utc, updated.period_end_utc) == (1_000, 1_000)


@pytest.mark.requirement("evaluation:R5")
def test_should_accept_period_collapsed_to_one_occurrence():
    """R5: a period may be a tight window around a single session."""
    payload = EvaluationCreate.model_validate(
        {
            "templateId": 1,
            "createdFor": "alice",
            "eventId": 3,
            "periodStartUtc": 1_000,
            "periodEndUtc": 1_001,
        }
    )

    assert payload.period_end_utc == 1_001


def test_should_round_trip_an_answer_and_default_its_fields_to_none():
    """An answer's fields are all optional on the wire."""
    empty = EvaluationAnswerInput.model_validate({})
    answer = EvaluationAnswerInput.model_validate(
        {"choices": ["a", "b"], "coachNote": "note"}
    )

    assert (empty.value_num, empty.value_text, empty.choices, empty.coach_note) == (
        None,
        None,
        None,
        None,
    )
    assert (
        EvaluationAnswerInput.model_validate(answer.model_dump(by_alias=True)) == answer
    )


def test_should_require_an_owner_on_transfer():
    """A transfer names the coach it goes to."""
    with pytest.raises(ValidationError):
        _ = EvaluationTransferRequest.model_validate({})
    assert EvaluationTransferRequest.model_validate({"owner": "c"}).owner == "c"
