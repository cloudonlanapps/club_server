"""Schemas for the extended profile and the club's profile options (#92).

The extended fields join the profile edit (``UserUpdate``) and the private
profile (``UserPrivateResponse``) through the two field sets below, so
neither of those schemas restates them.
"""

import json
from typing import Annotated, Any, ClassVar

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ..db.models.user import User
from .common import CamelCaseModel, to_camel
from .language import ENGLISH, code_for_name, is_language_code

NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
MAX_CHOICES = 50


def _distinct_choices(values: list[str]) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError("a value is listed twice")
    return values


# A member's preferred positions or languages: distinct values (#106).
ChoiceList = Annotated[
    list[Annotated[str, StringConstraints(min_length=1, max_length=100)]],
    Field(max_length=MAX_CHOICES),
    AfterValidator(_distinct_choices),
]


def encode_choices(values: list[str] | None) -> str | None:
    """A choice list as stored: JSON text, NULL for none."""
    return json.dumps(values) if values else None


def decode_choices(raw: str | None) -> list[str]:
    """A stored choice list; empty for none."""
    return [str(v) for v in json.loads(raw)] if raw else []


class Guardian(CamelCaseModel):
    """A member's parent or guardian, separate from the emergency contact.

    The phone is checked as the user's own phone is (users R65).
    """

    name: str | None = Field(None, max_length=100)
    relation: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=20)


def _code(value: str) -> str:
    if not is_language_code(value):
        raise ValueError(f"{value!r} is not a language code")
    return value


LanguageCodeText = Annotated[str, AfterValidator(_code)]


class ProfilePosition(CamelCaseModel):
    """One of the club's positions: its stored value and its name per language.

    The name needs English, and each key is one of the club's languages
    (``ProfileOptions``), so a form prints it in every print language that
    has one and in English otherwise (users R69b, #103).
    """

    value: NonEmptyText
    label: dict[NonEmptyText, NonEmptyText] = Field(min_length=1)

    @model_validator(mode="after")
    def _english(self) -> "ProfilePosition":
        if ENGLISH not in self.label:
            raise ValueError(f"position {self.value!r} has no English label")
        return self


class ProfileLanguage(CamelCaseModel):
    """One of the club's languages: its code (translations are keyed by it)
    and its name, which a member chooses (#133)."""

    code: LanguageCodeText
    name: NonEmptyText


class ProfileOptions(CamelCaseModel):
    """The ``profile_options`` preference: the club's positions and languages
    (users R69–R69b, applications R28). English is always a language."""

    positions: list[ProfilePosition]
    languages: list[ProfileLanguage]

    @field_validator("languages", mode="before")
    @classmethod
    def _coded(cls, value: Any) -> Any:
        """A language written by name alone takes its code (#133)."""
        if not isinstance(value, list):
            return value
        entries: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
        return [
            {"code": code_for_name(e), "name": e} if isinstance(e, str) else e
            for e in entries
        ]

    @model_validator(mode="after")
    def _distinct(self) -> "ProfileOptions":
        values = [position.value for position in self.positions]
        if len(set(values)) != len(values):
            raise ValueError("two positions share a value")
        codes = self.codes()
        if len(set(codes)) != len(codes):
            raise ValueError("a language code is listed twice")
        names = [language.name for language in self.languages]
        if len(set(names)) != len(names):
            raise ValueError("a language is listed twice")
        if ENGLISH not in codes:
            raise ValueError("English (en) is always one of the languages")
        for position in self.positions:
            unknown = sorted(set(position.label) - set(codes))
            if unknown:
                raise ValueError(
                    f"position {position.value!r} has labels in languages the "
                    + "club does not have: "
                    + ", ".join(unknown)
                )
        return self

    def codes(self) -> list[str]:
        """The club's language codes, in order."""
        return [language.code for language in self.languages]


# Schema field → ``users`` column, for each extended field.
EXTENDED_COLUMNS: dict[str, str] = {
    "club_district": "club_district",
    "preferred_positions": "preferred_positions",
    "past_experience": "past_experience",
    "nationality": "nationality",
    "preferred_languages": "preferred_languages",
    "guardian": "guardian",
    "membership_date_utc": "membership_date",
}

# The extended fields that hold a list of the club's options (#106).
CHOICE_COLUMNS: tuple[str, ...] = ("preferred_positions", "preferred_languages")


class ExtendedProfileUpdate(CamelCaseModel):
    """The extended fields of a profile edit; each optional, null clears it."""

    club_district: str | None = Field(None, max_length=200)
    preferred_positions: ChoiceList | None = None
    past_experience: str | None = Field(None, max_length=4000)
    nationality: str | None = Field(None, max_length=100)
    preferred_languages: ChoiceList | None = None
    guardian: Guardian | None = None
    membership_date_utc: int | None = None

    def extended_changes(self) -> dict[str, object]:
        """``users`` column → new value, for each extended field sent.

        The guardian is stored as JSON text, as the address is; a choice
        list as a JSON list, NULL when empty.
        """
        changes: dict[str, object] = {}
        for field, column in EXTENDED_COLUMNS.items():
            if field not in self.model_fields_set:
                continue
            value: Any = getattr(self, field)
            if isinstance(value, Guardian):
                value = value.model_dump_json()
            elif column in CHOICE_COLUMNS:
                choices: list[str] | None = value
                value = encode_choices(choices)
            changes[column] = value
        return changes


class ExtendedProfileFields(CamelCaseModel):
    """The extended fields of the private profile (users R63a)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    club_district: str | None = None
    preferred_positions: list[str] = Field(default_factory=list)
    past_experience: str | None = None
    nationality: str | None = None
    preferred_languages: list[str] = Field(default_factory=list)
    guardian: Guardian | None = None
    membership_date_utc: int | None = None

    @staticmethod
    def values_of(user: User) -> dict[str, Any]:
        """The extended fields of ``user``, by schema field name."""
        return {
            "club_district": user.club_district,
            "preferred_positions": decode_choices(user.preferred_positions),
            "past_experience": user.past_experience,
            "nationality": user.nationality,
            "preferred_languages": decode_choices(user.preferred_languages),
            "guardian": Guardian.model_validate(json.loads(user.guardian))
            if user.guardian
            else None,
            "membership_date_utc": user.membership_date,
        }
