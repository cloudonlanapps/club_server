"""Application template schemas (#93, `docs/applications_requirements.md`).

Top-level fields are camelCase as everywhere in the API. Every printed
text is custom text, ``{"en": "...", "mr": "..."}`` (language code → text);
a header item may also be a list of them, printed one per line. A text may
hold ``{name}`` gaps naming the template's facts, stored as written and
filled when the form is drawn (#126); a template keeps no link to the
library it was pasted from (#107).

Facts are ``{name: {"type": ..., "value" | "from" + "to": ...}}``; a date is a
UTC-midnight millisecond timestamp, as dates of birth are.
"""

import re
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from ..services.application_catalogue import catalogue_languages, labels
from .common import CamelCaseModel

MS_PER_DAY = 86_400_000
ENGLISH = "en"
MAX_PRINT_LANGUAGES = 2
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

ApplicationStatus = Literal["draft", "saved", "published"]
FactType = Literal["date", "date_range", "time", "amount", "name", "choices"]
GenderValue = Literal["male", "female", "other", "prefer_not_to_say"]


def _language(code: str) -> str:
    if code not in catalogue_languages():
        raise ValueError(f"{code!r} is not a language of the label catalogue")
    return code


def _label_id(key: str) -> str:
    if key not in labels():
        raise ValueError(f"{key!r} is not a standard label")
    return key


def _midnight(value: int | None) -> int | None:
    if value is not None and value % MS_PER_DAY != 0:
        raise ValueError("a date must be a UTC midnight (ms divisible by 86400000)")
    return value


LanguageCode = Annotated[str, AfterValidator(_language)]
NonEmpty = Annotated[str, Field(min_length=1)]
LanguageText = Annotated[dict[LanguageCode, NonEmpty], Field(min_length=1)]
UtcDate = Annotated[int, AfterValidator(_midnight)]


class TemplatePart(CamelCaseModel):
    """Nested template content: an optional key left unset is left out, so
    a part reads back in the shape it was sent."""

    @model_serializer(mode="wrap")
    def _compact(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped: dict[str, Any] = handler(self)
        return {k: v for k, v in dumped.items() if v is not None}


PrintedText = LanguageText
HeaderItem = Annotated[
    PrintedText | list[PrintedText], Field(union_mode="left_to_right")
]


class CustomTerm(TemplatePart):
    """A rule written for this template: its heading and its text."""

    heading: PrintedText
    body: PrintedText


class TermsNote(TemplatePart):
    """A closing note under the terms, with an optional attribution."""

    text: PrintedText
    attribution: PrintedText | None = None


Term = CustomTerm
TermsNoteEntry = TermsNote


class AgeCategory(TemplatePart):
    """An age group: born within the cutoffs (inclusive), of one of the genders."""

    label: LanguageText
    born_on_or_after_utc: UtcDate | None = None
    born_on_or_before_utc: UtcDate | None = None
    genders: list[GenderValue] | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "AgeCategory":
        """A band may overlap another, but not end before it starts (R6b)."""
        after, before = self.born_on_or_after_utc, self.born_on_or_before_utc
        if after is not None and before is not None and after > before:
            raise ValueError("bornOnOrAfterUtc is later than bornOnOrBeforeUtc")
        return self


class Fact(TemplatePart):
    """A value typed once and printed wherever a gap names it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        populate_by_name=True, extra="forbid"
    )

    type: FactType
    value: Any = None
    from_: UtcDate | None = Field(default=None, alias="from")
    to: UtcDate | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Fact":
        """Each type carries its own value (applications R6a)."""
        ranged = self.from_ is not None or self.to is not None
        if self.type == "date_range":
            if self.from_ is None or self.to is None or self.value is not None:
                raise ValueError("a date_range has from and to, and no value")
            if self.from_ > self.to:
                raise ValueError("a date_range ends on or after it starts")
            return self
        if ranged:
            raise ValueError(f"a {self.type} has a value, not from and to")
        _check_value(self.type, self.value)
        return self


def _check_value(kind: str, value: Any) -> None:
    """Refuse a fact value its type cannot print."""
    if kind == "date":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("a date's value is a UTC-midnight ms timestamp")
        _ = _midnight(value)
    elif kind == "time":
        if not isinstance(value, str) or not TIME_PATTERN.match(value):
            raise ValueError("a time's value is 'H:MM' on a 24-hour clock")
    elif kind == "amount":
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("an amount's value is a whole number, 0 or more")
    elif kind == "name":
        _check_name(value)
    elif not (
        isinstance(value, list)
        and value
        and all(isinstance(v, str) and v for v in value)  # pyright: ignore[reportUnknownVariableType]
    ):
        raise ValueError("a choices value is a non-empty list of choice ids")


def _check_name(value: Any) -> None:
    if isinstance(value, str) and value:
        return
    if isinstance(value, dict):
        texts: dict[Any, Any] = dict(value)  # pyright: ignore[reportUnknownArgumentType]
        if texts.get(ENGLISH) and all(
            isinstance(k, str)
            and k in catalogue_languages()
            and isinstance(v, str)
            and v
            for k, v in texts.items()
        ):
            return
    raise ValueError("a name's value is text, or {language: text} with English")


def _print_languages(value: list[str]) -> list[str]:
    if not value or value[0] != ENGLISH or len(value) > MAX_PRINT_LANGUAGES:
        raise ValueError('languages must be ["en"] or ["en", <one other>]')
    if len(value) == MAX_PRINT_LANGUAGES and value[1] == ENGLISH:
        raise ValueError("the second language is not English")
    return value


PrintLanguages = Annotated[list[LanguageCode], AfterValidator(_print_languages)]
LabelOverrides = dict[Annotated[str, AfterValidator(_label_id)], LanguageText]


class ApplicationContent(CamelCaseModel):
    """Everything an admin writes on a template."""

    languages: PrintLanguages = Field(default_factory=lambda: [ENGLISH])
    facts: dict[NonEmpty, Fact] = Field(default_factory=dict)
    organisation: PrintedText | None = None
    title: PrintedText | None = None
    subtitle: PrintedText | None = None
    header: list[HeaderItem] = Field(default_factory=list)
    age_categories: list[AgeCategory] = Field(default_factory=list)
    selections: list[PrintedText] = Field(default_factory=list)
    declaration: PrintedText | None = None
    form_footer: PrintedText | None = None
    terms: list[Term] = Field(default_factory=list)
    terms_notes: list[TermsNoteEntry] = Field(default_factory=list)
    label_overrides: LabelOverrides = Field(default_factory=dict)


class ApplicationCreate(ApplicationContent):
    """A new draft; every field may be filled in later."""


class ApplicationUpdate(CamelCaseModel):
    """Change a draft: the fields sent replace the stored ones; null clears."""

    languages: PrintLanguages | None = None
    facts: dict[NonEmpty, Fact] | None = None
    organisation: PrintedText | None = None
    title: PrintedText | None = None
    subtitle: PrintedText | None = None
    header: list[HeaderItem] | None = None
    age_categories: list[AgeCategory] | None = None
    selections: list[PrintedText] | None = None
    declaration: PrintedText | None = None
    form_footer: PrintedText | None = None
    terms: list[Term] | None = None
    terms_notes: list[TermsNoteEntry] | None = None
    label_overrides: LabelOverrides | None = None


class ApplicationSpread(CamelCaseModel):
    """The spare height added to each gap of page 1, in points."""

    with_guardian: float
    without_guardian: float


class ApplicationLayout(CamelCaseModel):
    """The layout decided on save (applications R16)."""

    name: str
    spread: ApplicationSpread


class ApplicationResponse(ApplicationContent):
    """A template as returned; ``publish_id`` only while it is published."""

    id: int
    status: ApplicationStatus
    layout: ApplicationLayout | None = None
    # A member's read: the indexes of the age categories they fit (R11a).
    fitting_categories: list[int] | None = None
    publish_id: str | None = None
    created_by: str | None = None
    created_at_utc: int
    updated_at_utc: int


class ApplicationLabelsResponse(CamelCaseModel):
    """The label catalogue, to pre-fill the editor."""

    languages: list[str]
    labels: dict[str, dict[str, str]]


ClauseSource = Literal["builtin", "club"]


class ApplicationClause(CamelCaseModel):
    """One text the editor can paste: a built-in clause (``key``, its gaps'
    fact types) or a club library text (``id``, ``name``), each with its
    kind, heading (a rule's) and text per language, gaps as written."""

    source: ClauseSource
    key: str | None = None
    id: int | None = None
    kind: str
    name: str | None = None
    gaps: dict[str, str] | None = None
    heading: dict[str, str] | None = None
    text: dict[str, str]


class ApplicationClausesResponse(CamelCaseModel):
    """The built-in clauses, then the club's texts, for the editor's pickers."""

    languages: list[str]
    choices: dict[str, dict[str, str]]
    clauses: list[ApplicationClause]
