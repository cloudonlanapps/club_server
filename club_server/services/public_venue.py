"""Public venue projection for the website (#307, venue R22–R25).

Anonymous readers get live venues only, addressed by an opaque public id,
with the newest publicly viewable ``venue_image`` link as the image.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import VENUE_IMAGE_TAG
from ..db.models.media import Media
from ..db.models.media_links import VenueMediaLink
from ..db.models.venue import Venue
from ..exceptions import VenueNotFoundException
from ..schemas.media import MediaRef
from ..schemas.venue import PublicVenueResponse
from ..utils import generate_venue_public_id
from .media import can_view_media


async def resolve_public_venue_image(
    db: AsyncSession, venue_id: int
) -> MediaRef | None:
    """The venue's current image, only if anonymously viewable (R25).

    Takes the newest ``venue_image`` link; a private current image yields
    ``None`` rather than an older public one, mirroring the avatar rule.
    """
    media = (
        await db.execute(
            select(Media)
            .join(VenueMediaLink, VenueMediaLink.media_uuid == Media.uuid)
            .where(
                VenueMediaLink.venue_id == venue_id,
                VenueMediaLink.tag == VENUE_IMAGE_TAG,
            )
            .order_by(VenueMediaLink.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if media is not None and can_view_media(media, None):
        return MediaRef.from_model(media)
    return None


class PublicVenueService:
    """Read-only venue queries for anonymous callers."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def _live_venues(self) -> list[Venue]:
        result = await self.db.execute(
            select(Venue).where(Venue.deleted_at.is_(None)).order_by(Venue.name)
        )
        return list(result.scalars().all())

    async def _project(self, venue: Venue) -> PublicVenueResponse:
        image = await resolve_public_venue_image(self.db, venue.id)
        return PublicVenueResponse.from_model(venue, image=image)

    async def list_public_venues(self) -> list[PublicVenueResponse]:
        """Every live venue, by name (R22, R24)."""
        return [await self._project(v) for v in await self._live_venues()]

    async def get_public_venue(self, public_id: str) -> PublicVenueResponse:
        """One live venue by its public id; anything else is 404 (R23, R24)."""
        for venue in await self._live_venues():
            if generate_venue_public_id(venue.id) == public_id:
                return await self._project(venue)
        raise VenueNotFoundException(public_id)
