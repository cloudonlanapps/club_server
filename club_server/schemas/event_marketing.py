"""Basic marketing fields shared by the event request schemas (#409).

Four presentation fields on the core event: the short description a card
shows under the title, a stamp (badge text), and two bullet lists. They are
optional everywhere; ``None`` clears them on update.
"""

from pydantic import Field

from .common import CamelCaseModel

SHORT_DESCRIPTION_MAX = 300
STAMP_MAX = 60
BULLET_MAX = 200
BULLETS_MAX = 20

BASIC_MARKETING_FIELDS = ("short_description", "stamp", "highlights", "includes")


class BasicMarketingFields(CamelCaseModel):
    """Mixin carrying the four basic marketing fields on a request body."""

    short_description: str | None = Field(None, max_length=SHORT_DESCRIPTION_MAX)
    stamp: str | None = Field(None, max_length=STAMP_MAX)
    highlights: list[str] | None = Field(None, max_length=BULLETS_MAX)
    includes: list[str] | None = Field(None, max_length=BULLETS_MAX)
