"""Public staff page: consent joined with admin curation (#332, public R1–R7)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import USER_AVATAR_TAG
from ..db.models.media import Media
from ..db.models.media_links import UserMediaLink
from ..db.models.staff_listing import PublicStaffListing
from ..db.models.user import User, UserStatus
from ..exceptions import NotACoachException, UserNotFoundException
from ..schemas.common import UserRoles
from ..schemas.media import MediaRef
from ..schemas.staff_listing import StaffListingRow
from ..schemas.user import PublicProfileResponse
from ..utils import now_utc_ms
from .media import can_view_media


def roles_of(user: User) -> list[str]:
    """Parse a user's role list from the JSON ``roles`` column."""
    if not user.roles:
        return []
    try:
        return UserRoles.model_validate_json(user.roles).roles
    except ValueError:
        return []


async def resolve_public_avatar(db: AsyncSession, username: str) -> MediaRef | None:
    """The user's current avatar, only if it is publicly viewable.

    Newest ``user_avatar`` link; a private current avatar yields ``None``
    rather than an older public one the user has since replaced.
    """
    media = (
        await db.execute(
            select(Media)
            .join(UserMediaLink, UserMediaLink.media_uuid == Media.uuid)
            .where(
                UserMediaLink.username == username,
                UserMediaLink.tag == USER_AVATAR_TAG,
            )
            .order_by(UserMediaLink.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if media is not None and can_view_media(media, None):
        return MediaRef.from_model(media)
    return None


class StaffListingService:
    """Reads and writes of the curation rows, and the public page they shape."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    # --- public page ------------------------------------------------------

    async def _consenting_coaches(self) -> list[User]:
        result = await self.db.execute(
            select(User).where(
                User.status == UserStatus.active.value,
                User.deleted_at.is_(None),
                User.is_public_profile == 1,
            )
        )
        return [u for u in result.scalars().all() if "coach" in roles_of(u)]

    async def public_profile(self, user: User) -> PublicProfileResponse:
        """One coach's public projection, guest mark included."""
        avatar = await resolve_public_avatar(self.db, user.username)
        row = user.staff_listing
        return PublicProfileResponse.from_user(
            user,
            avatar=avatar,
            is_guest=bool(row.is_guest) if row is not None else False,
        )

    async def public_staff(
        self, *, include_guests: bool
    ) -> list[PublicProfileResponse]:
        """The staff page (R1–R3): consenting coaches, curated order, hidden withheld."""
        coaches = await self._consenting_coaches()
        shown: list[User] = []
        for user in coaches:
            row = user.staff_listing
            if row is not None and row.is_hidden:
                continue
            if row is not None and row.is_guest and not include_guests:
                continue
            shown.append(user)

        def sort_key(user: User) -> tuple[int, int, str]:
            row = user.staff_listing
            if row is not None and row.position is not None:
                return (0, row.position, user.username)
            return (1, 0, user.username)

        shown.sort(key=sort_key)
        return [await self.public_profile(u) for u in shown]

    # --- admin curation ---------------------------------------------------

    async def list_rows(self) -> list[StaffListingRow]:
        """Every curation row with its user (R4)."""
        result = await self.db.execute(
            select(PublicStaffListing, User)
            .join(User, User.username == PublicStaffListing.username)
            .order_by(PublicStaffListing.position.nulls_last(), User.username)
        )
        return [StaffListingRow.from_models(row, user) for row, user in result.all()]

    async def _coach_or_raise(self, username: str) -> User:
        user = (
            await self.db.execute(
                select(User).where(User.username == username, User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if user is None:
            raise UserNotFoundException(username)
        if "coach" not in roles_of(user):
            raise NotACoachException(username)
        return user

    async def upsert(
        self,
        username: str,
        *,
        position: int | None,
        is_guest: bool | None,
        is_hidden: bool | None,
        fields_set: set[str],
    ) -> StaffListingRow:
        """Create or update one coach's row; omitted fields keep their value (R4)."""
        user = await self._coach_or_raise(username)
        now = now_utc_ms()
        row = user.staff_listing
        if row is None:
            row = PublicStaffListing(
                username=username,
                position=None,
                is_guest=False,
                is_hidden=False,
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
            user.staff_listing = row
        if "position" in fields_set:
            row.position = position
        if "is_guest" in fields_set and is_guest is not None:
            row.is_guest = is_guest
        if "is_hidden" in fields_set and is_hidden is not None:
            row.is_hidden = is_hidden
        row.updated_at = now
        await self.db.flush()
        return StaffListingRow.from_models(row, user)

    async def mark_guest(self, user: User) -> None:
        """Write the guest row and publish the profile on the admin's authority (R6)."""
        now = now_utc_ms()
        user.staff_listing = PublicStaffListing(
            username=user.username,
            position=None,
            is_guest=True,
            is_hidden=False,
            created_at=now,
            updated_at=now,
        )
        user.is_public_profile = 1
        user.roles = UserRoles(
            roles=sorted(set(roles_of(user)) | {"coach"})
        ).model_dump_json()
        await self.db.flush()

    async def delete(self, username: str) -> None:
        """Remove the row; the coach becomes public, uncurated (R4)."""
        user = await self._coach_or_raise(username)
        if user.staff_listing is None:
            return
        await self.db.delete(user.staff_listing)
        user.staff_listing = None
        await self.db.flush()
