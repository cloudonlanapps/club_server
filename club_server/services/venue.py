"""Venue service for venue management operations."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.event import Event
from ..db.models.occurrence_override import OccurrenceOverride
from ..db.models.user import User, UserStatus
from ..db.models.venue import Venue
from ..exceptions import (
    DefaultVenueExistsException,
    HardDeleteNeedsSoftDeleteException,
    NothingToRestoreException,
    VenueHasEventsException,
    VenueNotFoundException,
)
from ..schemas.common import ChangeLog, PaginatedResponse, UserRoles
from ..schemas.venue import (
    VenueDeletionResponse,
    VenueResponse,
)
from .notification import NotificationEvent, NotificationService
from ..utils import now_utc_ms


class VenueService:
    """Service for venue management operations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)

    async def _list_admin_usernames(self) -> list[str]:
        """Active admins (including super-admins)."""
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        admins: list[str] = []
        for u in result.scalars().all():
            if u.is_super_admin:
                admins.append(u.username)
                continue
            if not u.roles:
                continue
            parsed = UserRoles.model_validate_json(u.roles)
            if "admin" in parsed.roles:
                admins.append(u.username)
        return admins

    async def _future_event_ids_at_venue(self, venue_id: int) -> list[int]:
        """Event ids that haven't ended yet and still reference this venue."""
        now = now_utc_ms()
        result = await self.db.execute(
            select(Event.id).where(
                Event.venue_id == venue_id,
                Event.deleted_at.is_(None),
                Event.end_time > now,
            )
        )
        return [row for (row,) in result.all()]

    async def get_venues(
        self,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[VenueResponse]:
        """Get a paginated list of venues."""
        count_query = (
            select(func.count()).select_from(Venue).where(Venue.deleted_at.is_(None))
        )
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            select(Venue)
            .where(Venue.deleted_at.is_(None))
            .offset(offset)
            .limit(limit)
            .order_by(Venue.name)
        )
        result = await self.db.execute(query)
        venues = result.scalars().all()

        return PaginatedResponse(
            items=[VenueResponse.from_model(v) for v in venues],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_deleted_venues(
        self,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[VenueResponse]:
        """List soft-deleted venues (admin only)."""
        count_query = (
            select(func.count()).select_from(Venue).where(Venue.deleted_at.isnot(None))
        )
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            select(Venue)
            .where(Venue.deleted_at.isnot(None))
            .offset(offset)
            .limit(limit)
            .order_by(Venue.deleted_at.desc())
        )
        result = await self.db.execute(query)
        venues = result.scalars().all()

        return PaginatedResponse(
            items=[VenueResponse.from_model(v) for v in venues],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def get_venue(self, venue_id: int) -> Venue:
        """Get a venue by ID (includes soft-deleted)."""
        result = await self.db.execute(select(Venue).where(Venue.id == venue_id))
        venue = result.scalar_one_or_none()

        if not venue:
            raise VenueNotFoundException(venue_id)

        return venue

    async def list_active_venues(self) -> list[Venue]:
        """List active venues (not deleted)."""
        query = select(Venue).where(Venue.deleted_at.is_(None)).order_by(Venue.name)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create_venue(
        self,
        name: str,
        is_default: bool = False,
        address: str | None = None,
        description: str | None = None,
        map_uri: str | None = None,
        is_featured: bool = False,
    ) -> Venue:
        """Create a new venue."""
        if is_default:
            existing_default = await self.db.execute(
                select(Venue).where(Venue.is_default == 1, Venue.deleted_at.is_(None))
            )
            if existing_default.scalar_one_or_none():
                raise DefaultVenueExistsException()

        now = now_utc_ms()
        venue = Venue(
            name=name,
            address=address,
            description=description,
            map_uri=map_uri,
            is_default=1 if is_default else 0,
            is_featured=1 if is_featured else 0,
            created_at=now,
            updated_at=now,
        )

        self.db.add(venue)
        await self.db.flush()
        return venue

    async def update_venue(
        self,
        venue_id: int,
        name: str | None = None,
        is_default: bool | None = None,
        address: str | None = None,
        description: str | None = None,
        map_uri: str | None = None,
        is_featured: bool | None = None,
        fields_set: set[str] | None = None,
    ) -> tuple[Venue, ChangeLog]:
        """Update a venue. Returns (venue, changes)."""
        result = await self.db.execute(
            select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
        )
        venue = result.scalar_one_or_none()

        if not venue:
            raise VenueNotFoundException(venue_id)

        if is_default is True and not venue.is_default:
            existing_default = await self.db.execute(
                select(Venue).where(
                    Venue.is_default == 1,
                    Venue.deleted_at.is_(None),
                    Venue.id != venue_id,
                )
            )
            default_venue = existing_default.scalar_one_or_none()
            if default_venue:
                default_venue.is_default = 0

        fields_set = fields_set or set()
        changes = ChangeLog()
        old_name = venue.name
        if name is not None:
            changes.add("name", venue.name, name)
            venue.name = name
        if "address" in fields_set:
            changes.add("address", venue.address, address)
            venue.address = address
        if "description" in fields_set:
            changes.add("description", venue.description, description)
            venue.description = description
        if "map_uri" in fields_set:
            changes.add("map_uri", venue.map_uri, map_uri)
            venue.map_uri = map_uri
        if is_default is not None:
            changes.add("is_default", bool(venue.is_default), is_default)
            venue.is_default = 1 if is_default else 0
        if is_featured is not None:
            changes.add("is_featured", bool(venue.is_featured), is_featured)
            venue.is_featured = 1 if is_featured else 0

        now = now_utc_ms()
        venue.updated_at = now

        await self.db.flush()

        if name is not None and name != old_name:
            affected = await self._future_event_ids_at_venue(venue.id)
            if affected:
                await self._notifications.notify_for_event(
                    NotificationEvent(
                        type="venue.renamed",
                        recipients=await self._list_admin_usernames(),
                        data={
                            "venueId": venue.id,
                            "oldName": old_name,
                            "newName": name,
                            "affectedEventIds": affected,
                        },
                    )
                )

        return venue, changes

    async def restore_venue(self, venue_id: int) -> Venue:
        """Restore a soft-deleted venue."""
        result = await self.db.execute(select(Venue).where(Venue.id == venue_id))
        venue = result.scalar_one_or_none()

        if not venue:
            raise VenueNotFoundException(venue_id)

        if venue.deleted_at is None:
            raise NothingToRestoreException("Venue", venue_id)

        venue.deleted_at = None
        venue.updated_at = now_utc_ms()
        await self.db.flush()
        return venue

    async def soft_delete_venue(self, venue_id: int) -> Venue:
        """Soft delete a venue by setting deleted_at.

        Blocked if any active (non-deleted) events reference this venue
        via Event.venue_id or OccurrenceOverride.new_venue_id.
        """
        result = await self.db.execute(
            select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
        )
        venue = result.scalar_one_or_none()
        if not venue:
            raise VenueNotFoundException(venue_id)

        # Count active events referencing this venue directly
        direct_count_result = await self.db.execute(
            select(func.count())
            .select_from(Event)
            .where(
                Event.venue_id == venue_id,
                Event.deleted_at.is_(None),
            )
        )
        direct_count = direct_count_result.scalar_one()

        # Count active events referencing this venue via occurrence overrides
        override_count_result = await self.db.execute(
            select(func.count())
            .select_from(OccurrenceOverride)
            .join(Event, OccurrenceOverride.event_id == Event.id)
            .where(
                OccurrenceOverride.new_venue_id == venue_id,
                Event.deleted_at.is_(None),
            )
        )
        override_count = override_count_result.scalar_one()

        event_count = direct_count + override_count
        if event_count > 0:
            raise VenueHasEventsException(venue_id, event_count)

        now = now_utc_ms()
        venue.deleted_at = now
        venue.updated_at = now
        await self.db.flush()
        return venue

    async def hard_delete_venue(self, venue_id: int) -> VenueDeletionResponse:
        """
        Hard delete a venue (wipeout). SuperAdmin only.

        Requires the venue to be soft-deleted first (deleted_at must be set).
        Permanently removes venue from database.
        Blocked if ANY events reference this venue (past or future).

        Args:
            venue_id: The venue ID to delete.
        """
        result = await self.db.execute(select(Venue).where(Venue.id == venue_id))
        venue = result.scalar_one_or_none()

        if not venue:
            raise VenueNotFoundException(venue_id)

        if venue.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("Venue", venue_id)

        # Check if any events reference this venue (active or soft-deleted)
        direct_count_result = await self.db.execute(
            select(func.count()).select_from(Event).where(Event.venue_id == venue_id)
        )
        direct_count = direct_count_result.scalar_one()

        override_count_result = await self.db.execute(
            select(func.count())
            .select_from(OccurrenceOverride)
            .where(OccurrenceOverride.new_venue_id == venue_id)
        )
        override_count = override_count_result.scalar_one()

        event_count = direct_count + override_count
        if event_count > 0:
            raise VenueHasEventsException(venue_id, event_count)

        venue_name = venue.name
        await self.db.delete(venue)
        await self.db.flush()

        return VenueDeletionResponse(
            name=venue_name,
            reassigned_to=None,
        )
