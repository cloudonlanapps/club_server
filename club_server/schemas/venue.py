from typing import ClassVar
from pydantic import ConfigDict, Field

from ..db.models.venue import Venue
from ..utils import generate_venue_public_id
from .common import CamelCaseModel
from .media import MediaRef


class VenueCreate(CamelCaseModel):
    """Schema for creating a venue."""

    name: str = Field(..., min_length=1, max_length=100)
    address: str | None = None
    description: str | None = None
    map_uri: str | None = Field(None, max_length=500)
    is_default: bool = False
    is_featured: bool = False


class VenueUpdate(CamelCaseModel):
    """Schema for updating a venue."""

    name: str | None = Field(None, min_length=1, max_length=100)
    address: str | None = None
    description: str | None = None
    map_uri: str | None = Field(None, max_length=500)
    is_default: bool | None = None
    is_featured: bool | None = None


class VenueResponse(CamelCaseModel):
    """Schema for venue response."""

    id: int
    name: str
    address: str | None
    description: str | None
    map_uri: str | None
    is_default: bool
    is_featured: bool
    created_at_utc: int
    updated_at_utc: int
    deleted_at_utc: int | None

    # Server-derived badge for primary/default venue
    primary_venue_badge: str | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(
        cls,
        venue: Venue,
        primary_venue_badge: str = "Primary Training Venue",
    ) -> "VenueResponse":
        """Create response from Venue model.

        Args:
            venue: The Venue database model.
            primary_venue_badge: Badge text for default venue (default: "Primary Training Venue").
        """
        return cls(
            id=venue.id,
            name=venue.name,
            address=venue.address,
            description=venue.description,
            map_uri=venue.map_uri,
            is_default=bool(venue.is_default),
            is_featured=bool(venue.is_featured),
            created_at_utc=venue.created_at,
            updated_at_utc=venue.updated_at,
            deleted_at_utc=venue.deleted_at,
            primary_venue_badge=primary_venue_badge if venue.is_default else None,
        )


class PublicVenueResponse(CamelCaseModel):
    """The website's view of a venue (#307, venue R22–R25).

    Addressed by ``public_id`` and never by the integer id, so anonymous
    callers cannot walk venues. ``image`` is the newest ``venue_image`` link
    whose media is publicly viewable, else ``None``.
    ``map_uri`` is passed through as stored (``embed|share`` form).
    """

    public_id: str
    name: str
    address: str | None
    description: str | None
    map_uri: str | None
    is_default: bool
    is_featured: bool
    primary_venue_badge: str | None = None
    image: MediaRef | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(
        cls,
        venue: Venue,
        *,
        image: MediaRef | None,
        primary_venue_badge: str = "Primary Training Venue",
    ) -> "PublicVenueResponse":
        """Project a live venue for anonymous readers."""
        return cls(
            public_id=generate_venue_public_id(venue.id),
            name=venue.name,
            address=venue.address,
            description=venue.description,
            map_uri=venue.map_uri,
            is_default=bool(venue.is_default),
            is_featured=bool(venue.is_featured),
            primary_venue_badge=primary_venue_badge if venue.is_default else None,
            image=image,
        )


class VenueConflictItem(CamelCaseModel):
    """Schema for a single venue conflict."""

    event_id: int
    event_title: str
    start_time_utc: int
    end_time_utc: int


class ConflictReport(CamelCaseModel):
    """Schema for conflict check response."""

    has_conflict: bool
    venue_conflicts: list[VenueConflictItem] = []
    user_conflicts: list[VenueConflictItem] = []


class VenueDeletionResponse(CamelCaseModel):
    """Schema for venue deletion result."""

    name: str
    reassigned_to: int | None
