"""The club's profile options, the ``profile_options`` system preference (#92).

Positions and languages a member chooses from for ``preferred_positions``
and ``preferred_languages`` (users R64, R69–R69c). Stored in the generic
preference store and written only through the super admin's
``PATCH /v1/admin/preferences/profile_options`` (#98), so a write is
audited like any other (platform R7).
"""

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.backfills.profile_languages import coded_profile_options
from ..exceptions import InvalidPreferenceValueException
from ..schemas.extended_profile import ProfileLanguage, ProfileOptions
from .system_preferences import SystemPreferenceService

PROFILE_OPTIONS_KEY = "profile_options"
# The languages of a club that has written none: English and Marathi (#133).
DEFAULT_LANGUAGES = (
    ProfileLanguage(code="en", name="English"),
    ProfileLanguage(code="mr", name="Marathi"),
)


def validate_profile_options_value(value: Any) -> None:
    """Refuse a generic preference write the profile options could not use."""
    try:
        _ = ProfileOptions.model_validate(value)
    except ValidationError as error:
        raise InvalidPreferenceValueException(
            PROFILE_OPTIONS_KEY,
            "must be {positions: [{value, label}], languages: [{code, name}]}: "
            + "; ".join(str(e["msg"]) for e in error.errors()),
        ) from error


def stored_profile_options(value: Any) -> Any:
    """A write's value as stored: every language with its code (#133)."""
    return ProfileOptions.model_validate(value).model_dump(mode="json")


class ProfileOptionsService:
    """Read and write the club's profile options."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._prefs: SystemPreferenceService = SystemPreferenceService(db)

    async def read(self) -> ProfileOptions:
        """The options in force: until written, no positions and English and
        Marathi (R69); names stored before #133 read with their codes."""
        stored = (await self._prefs.read(PROFILE_OPTIONS_KEY)).value
        if stored is None:
            return ProfileOptions(positions=[], languages=list(DEFAULT_LANGUAGES))
        return ProfileOptions.model_validate(coded_profile_options(stored))

    async def languages(self) -> list[str]:
        """The club's language codes, English first as written (applications
        R28)."""
        return (await self.read()).codes()

    async def allows(self, column: str, value: str) -> bool:
        """Whether ``value`` is one of the club's options for ``column``."""
        options = await self.read()
        if column == "preferred_positions":
            return value in {position.value for position in options.positions}
        return value in {language.name for language in options.languages}
