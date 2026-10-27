"""The club's profile options, the ``profile_options`` system preference (#92).

Positions and languages a member chooses from for ``preferred_position``
and ``preferred_language`` (users R64, R69–R69c). Stored in the generic
preference store and written only through the super admin's
``PATCH /v1/admin/preferences/profile_options`` (#98), so a write is
audited like any other (platform R7).
"""

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import InvalidPreferenceValueException
from ..schemas.extended_profile import ProfileOptions
from .system_preferences import SystemPreferenceService

PROFILE_OPTIONS_KEY = "profile_options"


def validate_profile_options_value(value: Any) -> None:
    """Refuse a generic preference write the profile options could not use."""
    try:
        _ = ProfileOptions.model_validate(value)
    except ValidationError as error:
        raise InvalidPreferenceValueException(
            PROFILE_OPTIONS_KEY,
            "must be {positions: [{value, label}], languages: [name]}: "
            + "; ".join(str(e["msg"]) for e in error.errors()),
        ) from error


class ProfileOptionsService:
    """Read and write the club's profile options."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._prefs: SystemPreferenceService = SystemPreferenceService(db)

    async def read(self) -> ProfileOptions:
        """The options in force; both lists empty until written (R69)."""
        stored = (await self._prefs.read(PROFILE_OPTIONS_KEY)).value
        if stored is None:
            return ProfileOptions(positions=[], languages=[])
        return ProfileOptions.model_validate(stored)

    async def allows(self, column: str, value: str) -> bool:
        """Whether ``value`` is one of the club's options for ``column``."""
        options = await self.read()
        if column == "preferred_position":
            return value in {position.value for position in options.positions}
        return value in options.languages
