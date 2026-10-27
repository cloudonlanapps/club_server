"""The club's library of texts for application templates (#126).

A club text is pasted into a template as written, as a built-in clause is:
its kind (one of the built-in clauses' kinds), a short name for the
editor's picker, and its text per language, ``{name}`` gaps allowed. A rule
may carry a heading. The library is club-wide: one for every template.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .application import LanguageText
from .common import CamelCaseModel

# The kinds of the built-in clauses (assets/applications/clauses.json).
LibraryTextKind = Literal["header", "selection", "declaration", "rule", "note"]
RULE_KIND = "rule"
MAX_NAME_LENGTH = 100

LibraryTextName = Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)]


class ApplicationLibraryTextCreate(CamelCaseModel):
    """A new club text; ``heading`` only for a rule."""

    kind: LibraryTextKind
    name: LibraryTextName
    heading: LanguageText | None = None
    text: LanguageText

    @model_validator(mode="after")
    def _heading_for_a_rule(self) -> "ApplicationLibraryTextCreate":
        if self.heading is not None and self.kind != RULE_KIND:
            raise ValueError("only a rule has a heading")
        return self


class ApplicationLibraryTextUpdate(CamelCaseModel):
    """Change a club text: the fields sent replace the stored ones; null
    clears the heading, and the result must still be a valid text."""

    kind: LibraryTextKind | None = None
    name: LibraryTextName | None = None
    heading: LanguageText | None = None
    text: LanguageText | None = None


class ApplicationLibraryTextResponse(ApplicationLibraryTextCreate):
    """A club text as returned; ``key`` names the built-in clause a seeded
    text came from (#133), null for one the club wrote."""

    id: int
    key: str | None = None
    created_by: str | None = None
    created_at_utc: int
    updated_at_utc: int


class ApplicationClausesResponse(CamelCaseModel):
    """The club's library texts for the editor's pickers, and the languages
    and choices a fact is printed in (R7, #133)."""

    languages: list[str]
    choices: dict[str, dict[str, str]]
    clauses: list[ApplicationLibraryTextResponse]
