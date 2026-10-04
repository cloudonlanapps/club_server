"""Evaluation schemas (#302, #535)."""

from pydantic import Field, model_validator

from .common import CamelCaseModel
from .evaluation_item import EvaluationTemplateItemSchema
from .evaluation_template import EvaluationLayoutEntry


# The request fields that make up a period, as model field names.
PERIOD_FIELDS = frozenset({"period_start_utc", "period_end_utc"})


def check_period(start: int | None, end: int | None) -> None:
    """Both bounds or neither, the end not before the start (R4).

    A one-day period may end where it starts. That the end lies in the past
    needs the clock, so the service checks it (``PERIOD_IN_FUTURE``).
    """
    if (start is None) != (end is None):
        raise ValueError("a period needs both a start and an end")
    if start is not None and end is not None and end < start:
        raise ValueError("periodEndUtc must not be before periodStartUtc")


class EvaluationCreate(CamelCaseModel):
    """Create a draft. No event means a general evaluation (R1, R50)."""

    template_id: int
    created_for: str = Field(min_length=1, max_length=50)
    event_id: int | None = None
    period_start_utc: int | None = None
    period_end_utc: int | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "EvaluationCreate":
        """A period, on either scope, is well formed (R4)."""
        check_period(self.period_start_utc, self.period_end_utc)
        return self


class EvaluationUpdate(CamelCaseModel):
    """Change a draft's event, its period, or both (R21, R23).

    An omitted field keeps its value. ``eventId: null`` makes the draft
    general; both period bounds null clear the period. The member is fixed.
    """

    event_id: int | None = None
    period_start_utc: int | None = None
    period_end_utc: int | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "EvaluationUpdate":
        """Same rule as on create."""
        check_period(self.period_start_utc, self.period_end_utc)
        return self


class EvaluationAnswerInput(CamelCaseModel):
    """One answer, replacing any earlier one. Which value applies is the item's type."""

    value_num: float | None = None
    value_text: str | None = None
    choices: list[str] | None = None
    coach_note: str | None = None


class EvaluationEvidence(CamelCaseModel):
    """One file attached to an answer (R56a)."""

    media_uuid: str
    metadata: str | None = None


class EvaluationAnswerResponse(CamelCaseModel):
    """An answer as read: its value, note and evidence, any of them alone (R9)."""

    item_id: int
    value_num: float | None = None
    value_text: str | None = None
    choices: list[str] = []
    coach_note: str | None = None
    evidence: list[EvaluationEvidence] = []


class EvaluationTransferRequest(CamelCaseModel):
    """Hand an unpublished evaluation to another coach (R36)."""

    owner: str = Field(min_length=1, max_length=50)


class EvaluationStaffView(CamelCaseModel):
    """The whole evaluation, private items included — its owner only (R41)."""

    id: int
    template_id: int
    created_for: str
    created_by: str
    owner: str | None = None
    event_id: int | None = None
    period_start_utc: int | None = None
    period_end_utc: int | None = None
    status: str
    answers: list[EvaluationAnswerResponse]
    created_at_utc: int
    updated_at_utc: int
    published_at_utc: int | None = None
    deleted_at_utc: int | None = None


class EvaluationMemberTemplate(CamelCaseModel):
    """What the member needs of the template: public items and their layout (R39)."""

    id: int
    name: str
    layout: list[EvaluationLayoutEntry]
    items: list[EvaluationTemplateItemSchema]


class EvaluationMemberView(CamelCaseModel):
    """A published evaluation as the member sees it.

    A separate projection rather than a filtered one, so private items have
    no route to the member at all (R39).
    """

    id: int
    created_for: str
    created_by: str
    owner: str | None = None
    event_id: int | None = None
    period_start_utc: int | None = None
    period_end_utc: int | None = None
    status: str
    published_at_utc: int | None = None
    template: EvaluationMemberTemplate
    answers: list[EvaluationAnswerResponse]
