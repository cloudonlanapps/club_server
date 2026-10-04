"""Template items: one variant of a union selected by ``type`` (#535, R12, R12a).

Every variant forbids properties it does not declare, so an unknown type or a
property foreign to its type is refused with a 422 naming the field. A
variant's own consistency rules (R12a) run in its model validator.

``id`` is assigned by the server and ignored on input; ``originItemId`` is
set on a copy (R12b).
"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from .common import CamelCaseModel

DEFAULT_RATE_MIN = 1
DEFAULT_RATE_MAX = 5


def _check_comment_rule(
    show_comment_area: bool, required_for: list[object], possible: list[object]
) -> None:
    """A required coach note names answers the question can take, and needs its area."""
    if required_for and not show_comment_area:
        raise ValueError("requireCommentFor needs showCommentArea")
    for value in required_for:
        if value not in possible:
            raise ValueError(
                f"requireCommentFor names {value!r}, not a possible answer"
            )


class _ItemBase(CamelCaseModel):
    """What every item carries."""

    id: int | None = None
    is_private: bool = False


class _QuestionBase(_ItemBase):
    """What every question carries."""

    question: str = Field(min_length=1)
    is_required: bool = False
    allow_evidence: bool = False
    origin_item_id: int | None = None


class EvaluationRateLevel(CamelCaseModel):
    """One labelled level of a rating; values run 1..n in order."""

    value: int
    text: str = Field(min_length=1)


class EvaluationChoice(CamelCaseModel):
    """One choice of a choice question."""

    value: str = Field(min_length=1)
    text: str = Field(min_length=1)


class EvaluationRatingItem(_QuestionBase):
    """Stars or a range (``rateMin``..``rateMax``, default 1..5), or labelled levels."""

    type: Literal["rating"]
    show_comment_area: bool = False
    require_comment_for: list[int] = []
    rate_type: Literal["stars"] | None = None
    rate_min: int | None = None
    rate_max: int | None = None
    rate_values: list[EvaluationRateLevel] | None = None

    def scale(self) -> list[int]:
        """Every value this rating can take."""
        if self.rate_values is not None:
            return [level.value for level in self.rate_values]
        low = DEFAULT_RATE_MIN if self.rate_min is None else self.rate_min
        high = DEFAULT_RATE_MAX if self.rate_max is None else self.rate_max
        return list(range(low, high + 1))

    @model_validator(mode="after")
    def check_scale(self) -> "EvaluationRatingItem":
        """Levels or a range, never both; ordered; notes only for possible values."""
        if self.rate_values is not None:
            if self.rate_min is not None or self.rate_max is not None:
                raise ValueError("rateValues excludes rateMin and rateMax")
            if self.rate_type is not None:
                raise ValueError("stars take a range, not rateValues")
            values = [level.value for level in self.rate_values]
            if values != list(range(1, len(values) + 1)) or not values:
                raise ValueError("rateValues must be 1..n in order")
        else:
            if (self.rate_min is None) != (self.rate_max is None):
                raise ValueError("a range needs both rateMin and rateMax")
            if self.rate_min is not None and self.rate_max is not None:
                if self.rate_min >= self.rate_max:
                    raise ValueError("rateMin must be less than rateMax")
        _check_comment_rule(
            self.show_comment_area, list(self.require_comment_for), list(self.scale())
        )
        return self


class EvaluationYesNoItem(_QuestionBase):
    """A Yes / No question; an answer stores 1 for yes and 0 for no."""

    type: Literal["yesNo"]
    show_comment_area: bool = False
    require_comment_for: list[bool] = []
    label_true: str | None = None
    label_false: str | None = None

    @model_validator(mode="after")
    def check_comment(self) -> "EvaluationYesNoItem":
        """Notes may be required for yes, for no, or both."""
        _check_comment_rule(
            self.show_comment_area, list(self.require_comment_for), [True, False]
        )
        return self


class EvaluationChoiceItem(_QuestionBase):
    """A single- or multiple-choice question."""

    type: Literal["singleChoice", "multipleChoice"]
    show_comment_area: bool = False
    require_comment_for: list[str] = []
    choices: list[EvaluationChoice] = Field(min_length=1)

    @model_validator(mode="after")
    def check_choices(self) -> "EvaluationChoiceItem":
        """Choice values are unique; notes only for real choices."""
        values = [choice.value for choice in self.choices]
        if len(values) != len(set(values)):
            raise ValueError("choice values must be unique")
        _check_comment_rule(
            self.show_comment_area, list(self.require_comment_for), list(values)
        )
        return self


class EvaluationNumberItem(_QuestionBase):
    """A question answered with any number."""

    type: Literal["number"]
    show_comment_area: bool = False
    require_comment_for: list[float] = []

    @model_validator(mode="after")
    def check_comment(self) -> "EvaluationNumberItem":
        """A note requirement needs the comment area."""
        if self.require_comment_for and not self.show_comment_area:
            raise ValueError("requireCommentFor needs showCommentArea")
        return self


class EvaluationQaItem(_QuestionBase):
    """A question answered in writing (markdown)."""

    type: Literal["qa"]


class EvaluationInfoItem(_ItemBase):
    """Markdown shown to the reader; it asks nothing."""

    type: Literal["info"]
    markdown: str = Field(min_length=1)


EvaluationQuestionItem = (
    EvaluationRatingItem
    | EvaluationYesNoItem
    | EvaluationChoiceItem
    | EvaluationNumberItem
    | EvaluationQaItem
)

EvaluationTemplateItemSchema = Annotated[
    EvaluationQuestionItem | EvaluationInfoItem,
    Field(discriminator="type"),
]

template_item_adapter: TypeAdapter[EvaluationQuestionItem | EvaluationInfoItem] = (
    TypeAdapter(EvaluationTemplateItemSchema)
)
