"""Schemas for the extended profile and the club's profile options (#92).

The extended fields join the profile edit (``UserUpdate``) and the private
profile (``UserPrivateResponse``) through the two field sets below, so
neither of those schemas restates them.
"""

import json
from typing import Annotated, Any, ClassVar

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ..db.models.user import User
from .common import CamelCaseModel, to_camel

NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class Guardian(CamelCaseModel):
    """A member's parent or guardian, separate from the emergency contact.

    The phone is checked as the user's own phone is (users R65).
    """

    name: str | None = Field(None, max_length=100)
    relation: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=20)


ENGLISH = "en"


class ProfilePosition(CamelCaseModel):
    """One of the club's positions: its stored value and its name per language.

    The name needs English, and each key is a language of the application
    form's label catalogue, so a form prints it in every print language that
    has one and in English otherwise (users R69b, #103).
    """

    value: NonEmptyText
    label: dict[NonEmptyText, NonEmptyText] = Field(min_length=1)

    @model_validator(mode="after")
    def _languages(self) -> "ProfilePosition":
        # Imported here: the catalogue lives in the services package, which
        # imports the schemas.
        from ..services.application_catalogue import catalogue_languages

        if ENGLISH not in self.label:
            raise ValueError(f"position {self.value!r} has no English label")
        unknown = sorted(set(self.label) - set(catalogue_languages()))
        if unknown:
            raise ValueError(
                f"position {self.value!r} has labels in unknown languages: "
                + ", ".join(unknown)
            )
        return self


class ProfileOptions(CamelCaseModel):
    """The ``profile_options`` preference: the club's positions and languages
    (users R69–R69b)."""

    positions: list[ProfilePosition]
    languages: list[NonEmptyText]

    @model_validator(mode="after")
    def _distinct(self) -> "ProfileOptions":
        values = [position.value for position in self.positions]
        if len(set(values)) != len(values):
            raise ValueError("two positions share a value")
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("a language is listed twice")
        return self


# Schema field → ``users`` column, for each extended field.
EXTENDED_COLUMNS: dict[str, str] = {
    "club_district": "club_district",
    "preferred_position": "preferred_position",
    "past_experience": "past_experience",
    "nationality": "nationality",
    "preferred_language": "preferred_language",
    "guardian": "guardian",
    "membership_date_utc": "membership_date",
}

# The extended fields a user sets on their own profile; the membership date
# is an admin's alone (users R67).
SELF_EXTENDED_FIELDS: frozenset[str] = frozenset(EXTENDED_COLUMNS) - {
    "membership_date_utc"
}


class ExtendedProfileUpdate(CamelCaseModel):
    """The extended fields of a profile edit; each optional, null clears it."""

    club_district: str | None = Field(None, max_length=200)
    preferred_position: str | None = Field(None, max_length=100)
    past_experience: str | None = Field(None, max_length=4000)
    nationality: str | None = Field(None, max_length=100)
    preferred_language: str | None = Field(None, max_length=100)
    guardian: Guardian | None = None
    membership_date_utc: int | None = None

    def extended_changes(self) -> dict[str, object]:
        """``users`` column → new value, for each extended field sent.

        The guardian is stored as JSON text, as the address is.
        """
        changes: dict[str, object] = {}
        for field, column in EXTENDED_COLUMNS.items():
            if field not in self.model_fields_set:
                continue
            value = getattr(self, field)
            if isinstance(value, Guardian):
                value = value.model_dump_json()
            changes[column] = value
        return changes


class ExtendedProfileFields(CamelCaseModel):
    """The extended fields of the private profile (users R63a)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )

    club_district: str | None = None
    preferred_position: str | None = None
    past_experience: str | None = None
    nationality: str | None = None
    preferred_language: str | None = None
    guardian: Guardian | None = None
    membership_date_utc: int | None = None

    @staticmethod
    def values_of(user: User) -> dict[str, Any]:
        """The extended fields of ``user``, by schema field name."""
        return {
            "club_district": user.club_district,
            "preferred_position": user.preferred_position,
            "past_experience": user.past_experience,
            "nationality": user.nationality,
            "preferred_language": user.preferred_language,
            "guardian": Guardian.model_validate(json.loads(user.guardian))
            if user.guardian
            else None,
            "membership_date_utc": user.membership_date,
        }
