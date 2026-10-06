"""Public event catalogue (#299, public R8–R15)."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import EVENT_COVER_TAG, EVENT_GALLERY_TAG
from ..db.models.event import Event
from ..db.models.media import Media
from ..db.models.media_links import EventMediaLink
from ..db.models.user import User, UserStatus
from ..db.models.venue import Venue
from ..exceptions import EventNotFoundException
from ..schemas.common import PaginatedResponse
from ..schemas.event import EventResponse
from ..schemas.media import MediaRef
from ..schemas.public_event import PublicEventResponse
from ..schemas.user import PublicProfileResponse
from ..schemas.venue import PublicVenueResponse
from ..utils import generate_event_public_id, generate_venue_public_id, now_utc_ms
from .event_eligibility import event_window
from .event_listing import _still_running_at
from .lifecycle import is_past
from .media import can_view_media
from .public_venue import resolve_public_venue_image
from .staff_listing import StaffListingService, roles_of


async def resolve_public_coaches(
    db: AsyncSession, usernames: list[str]
) -> list[PublicProfileResponse]:
    """The consenting coaches among ``usernames``, in order, as public profiles (R11).

    Guests count as consenting (published by their admin). A coach who has
    not consented is omitted, never named.
    """
    if not usernames:
        return []
    result = await db.execute(
        select(User).where(
            User.username.in_(usernames),
            User.status == UserStatus.active.value,
            User.deleted_at.is_(None),
            User.is_public_profile == 1,
        )
    )
    by_name = {u.username: u for u in result.scalars().all() if "coach" in roles_of(u)}
    service = StaffListingService(db)
    return [await service.public_profile(by_name[n]) for n in usernames if n in by_name]


async def attach_public_coaches(
    db: AsyncSession, responses: list[EventResponse]
) -> None:
    """Fill ``coaches`` on authenticated event responses (R15)."""
    for response in responses:
        response.coaches = await resolve_public_coaches(db, response.coach_names or [])


async def resolve_public_event_media(
    db: AsyncSession, event_id: int
) -> tuple[MediaRef | None, list[MediaRef]]:
    """Newest public cover and every public gallery item in attachment order (R13)."""
    result = await db.execute(
        select(EventMediaLink, Media)
        .join(Media, Media.uuid == EventMediaLink.media_uuid)
        .where(
            EventMediaLink.event_id == event_id,
            EventMediaLink.tag.in_((EVENT_COVER_TAG, EVENT_GALLERY_TAG)),
        )
        .order_by(EventMediaLink.created_at)
    )
    cover: MediaRef | None = None
    gallery: list[MediaRef] = []
    for link, media in result.all():
        if link.tag == EVENT_COVER_TAG:
            # Newest wins, private newest hides older public ones.
            cover = MediaRef.from_model(media) if can_view_media(media, None) else None
        elif can_view_media(media, None):
            gallery.append(MediaRef.from_model(media))
    return cover, gallery


class PublicEventService:
    """Read-only catalogue queries for anonymous callers."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def _venue_projection(self, venue_id: int) -> PublicVenueResponse:
        venue = (
            await self.db.execute(select(Venue).where(Venue.id == venue_id))
        ).scalar_one()
        image = await resolve_public_venue_image(self.db, venue.id)
        return PublicVenueResponse.from_model(venue, image=image)

    async def _resolve_venue_public_id(self, public_id: str) -> int | None:
        result = await self.db.execute(
            select(Venue.id).where(Venue.deleted_at.is_(None))
        )
        for venue_id in result.scalars().all():
            if generate_venue_public_id(venue_id) == public_id:
                return venue_id
        return None

    async def project(self, event: Event, now_ms: int) -> PublicEventResponse:
        """One event with its venue, coaches, media and marketing block."""
        cover, gallery = await resolve_public_event_media(self.db, event.id)
        return PublicEventResponse.build(
            event,
            await event_window(self.db, event, now_ms),
            is_past=await is_past(self.db, event, now_ms),
            cover=cover,
            gallery=gallery,
            venue=await self._venue_projection(event.venue_id),
            coaches=await resolve_public_coaches(self.db, event.coach_names_list),
        )

    async def list_public_events(
        self,
        *,
        event_type: str | None,
        from_time_utc: int | None,
        to_time_utc: int | None,
        featured: bool | None,
        venue_public_id: str | None,
        offset: int,
        limit: int,
    ) -> PaginatedResponse[PublicEventResponse]:
        """Public, live events by type, venue, featured flag and window (R8, R9)."""
        query = select(Event).where(
            Event.visibility == "public", Event.deleted_at.is_(None)
        )
        if event_type:
            query = query.where(Event.type == event_type)
        if featured is not None:
            query = query.where(Event.is_featured.is_(featured))
        if venue_public_id is not None:
            venue_id = await self._resolve_venue_public_id(venue_public_id)
            if venue_id is None:
                return PaginatedResponse(items=[], total=0, offset=offset, limit=limit)
            query = query.where(Event.venue_id == venue_id)
        if from_time_utc is not None:
            query = query.where(
                and_(
                    or_(Event.until_time.is_(None), Event.until_time >= from_time_utc),
                    or_(
                        Event.start_time >= from_time_utc,
                        Event.end_time >= from_time_utc,
                        Event.rrule.isnot(None),
                    ),
                )
            )
        if to_time_utc is not None:
            query = query.where(Event.start_time <= to_time_utc)

        events = list(
            (await self.db.execute(query.order_by(Event.start_time))).scalars().all()
        )
        if from_time_utc is not None:
            events = [e for e in events if _still_running_at(e, from_time_utc)]
        now = now_utc_ms()
        return PaginatedResponse(
            items=[await self.project(e, now) for e in events[offset : offset + limit]],
            total=len(events),
            offset=offset,
            limit=limit,
        )

    async def get_public_event(self, public_id: str) -> PublicEventResponse:
        """One public, live event by public id; anything else is 404 (R10)."""
        result = await self.db.execute(
            select(Event).where(
                Event.visibility == "public", Event.deleted_at.is_(None)
            )
        )
        for event in result.scalars().all():
            if generate_event_public_id(event.id) == public_id:
                return await self.project(event, now_utc_ms())
        raise EventNotFoundException(public_id)
