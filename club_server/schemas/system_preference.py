"""Schemas for the admin system-preferences surface (#57)."""

from typing import Any, ClassVar
from pydantic import ConfigDict

from .common import CamelCaseModel


class SystemPreferenceResponse(CamelCaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=CamelCaseModel.model_config["alias_generator"],
        populate_by_name=True,
        from_attributes=True,
    )

    key: str
    value: Any
    updated_at_utc: int | None
    """Null for a key never written: it has no last-updated time (#518)."""
    updated_by: str | None


class SystemPreferenceList(CamelCaseModel):
    items: list[SystemPreferenceResponse]


class SystemPreferenceUpdate(CamelCaseModel):
    value: Any
