"""The website's view of an event (#299, public R8–R14).

Addressed by public id; no integer id, no usernames. The venue rides along
as its public projection, the coaches as public profiles, the media as
public uuids, and the basic marketing block when any of it is set.
"""

from typing import ClassVar

from pydantic import ConfigDict

from ..age_eligibility import Age, EligibilityWindow
from ..db.models.event import Event
from ..utils import generate_event_public_id, generate_venue_public_id
from .common import CamelCaseModel
from .event import EventResponse, Session
from .media import MediaRef
from .user import PublicProfileResponse
from .venue import PublicVenueResponse


class BasicMarketingBlock(CamelCaseModel):
    """The four basic marketing fields (marketing R1), as one object."""

    short_description: str | None = None
    stamp: str | None = None
    highlights: list[str] | None = None
    includes: list[str] | None = None

    @classmethod
    def from_response(cls, event: EventResponse) -> "BasicMarketingBlock | None":
        """None when none of the four is set (public R14)."""
        block = cls(
            short_description=event.short_description,
            stamp=event.stamp,
            highlights=event.highlights,
            includes=event.includes,
        )
        if all(getattr(block, name) is None for name in type(block).model_fields):
            return None
        return block


class PublicEventResponse(CamelCaseModel):
    """One catalogue entry or event page for anonymous readers."""

    public_id: str
    title: str
    description: str | None
    type: str
    venue_id: str
    rrule: str | None
    start_time_utc: int
    end_time_utc: int
    until_time_utc: int | None
    sessions: list[Session] | None
    gender: str | None
    min_age: Age | None
    max_age: Age | None
    strict_age: bool
    dob_on_or_after_utc: int | None
    dob_on_or_before_utc: int | None
    eligibility_reference_day_utc: int
    is_featured: bool
    is_past: bool
    cover: MediaRef | None
    gallery: list[MediaRef]
    venue: PublicVenueResponse
    coaches: list[PublicProfileResponse]
    marketing: BasicMarketingBlock | None
    created_at_utc: int
    updated_at_utc: int

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    @classmethod
    def build(
        cls,
        event: Event,
        window: EligibilityWindow,
        *,
        is_past: bool,
        cover: MediaRef | None,
        gallery: list[MediaRef],
        venue: PublicVenueResponse,
        coaches: list[PublicProfileResponse],
    ) -> "PublicEventResponse":
        """Project an event with its pre-resolved public companions."""
        base = EventResponse.from_model(event, window)
        return cls(
            public_id=generate_event_public_id(event.id),
            title=base.title,
            description=base.description,
            type=base.type,
            venue_id=generate_venue_public_id(base.venue_id),
            rrule=base.rrule,
            start_time_utc=base.start_time_utc,
            end_time_utc=base.end_time_utc,
            until_time_utc=base.until_time_utc,
            sessions=base.sessions,
            gender=base.gender,
            min_age=base.min_age,
            max_age=base.max_age,
            strict_age=base.strict_age,
            dob_on_or_after_utc=base.dob_on_or_after_utc,
            dob_on_or_before_utc=base.dob_on_or_before_utc,
            eligibility_reference_day_utc=base.eligibility_reference_day_utc,
            is_featured=base.is_featured,
            is_past=is_past,
            cover=cover,
            gallery=gallery,
            venue=venue,
            coaches=coaches,
            marketing=BasicMarketingBlock.from_response(base),
            created_at_utc=base.created_at_utc,
            updated_at_utc=base.updated_at_utc,
        )
