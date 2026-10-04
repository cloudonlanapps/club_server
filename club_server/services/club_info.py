"""Club info and site media for the public website (#296, public R16–R19).

Two system-preference keys. ``club_info`` is any JSON object the club
writes about itself; ``site_media`` maps a purpose the site defines to a
live, publicly viewable media item, validated on write so a slot can never
point at something the site cannot fetch. It is stored as a uuid and
published as a descriptor (#424).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.media import Media
from ..exceptions import (
    InvalidPreferenceValueException,
    SiteMediaNotPublicException,
    SystemPreferenceNotFoundException,
)
from ..mailer.config import email_settings
from ..schemas.club_info import PublicClubInfoResponse
from ..schemas.media import MediaRef
from .media import can_view_media
from .system_preferences import SystemPreferenceService

CLUB_INFO_KEY = "club_info"
SITE_MEDIA_KEY = "site_media"


async def validate_preference_value(db: AsyncSession, key: str, value: Any) -> None:
    """Refuse a value the public site could not use (R17, R18)."""
    if key == CLUB_INFO_KEY and not isinstance(value, dict):
        raise InvalidPreferenceValueException(key, "must be a JSON object")
    if key == SITE_MEDIA_KEY:
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise InvalidPreferenceValueException(
                key, "must map purpose strings to media uuids"
            )
        for purpose, media_uuid in value.items():
            media = (
                await db.execute(
                    select(Media).where(
                        Media.uuid == media_uuid, Media.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if media is None or not can_view_media(media, None):
                raise SiteMediaNotPublicException(purpose, media_uuid)


class ClubInfoService:
    """Reads of the two documents, for the public site and the mailer."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._prefs = SystemPreferenceService(db)

    async def _document(self, key: str) -> dict[str, Any]:
        try:
            value = await self._prefs.get_value(key)
        except SystemPreferenceNotFoundException:
            return {}
        return value if isinstance(value, dict) else {}

    async def _site_media(self) -> dict[str, MediaRef]:
        """Each slot as a descriptor, skipping any the site could not fetch.

        Write validation requires live public media, but media is deleted and
        made private afterwards; a stale slot is dropped rather than published
        as something a visitor would get a 403 for (#424).
        """
        stored = await self._document(SITE_MEDIA_KEY)
        uuids = [v for v in stored.values() if isinstance(v, str)]
        if not uuids:
            return {}
        rows = (
            await self.db.execute(
                select(Media).where(Media.uuid.in_(uuids), Media.deleted_at.is_(None))
            )
        ).scalars()
        by_uuid = {m.uuid: m for m in rows if can_view_media(m, None)}
        return {
            purpose: MediaRef.from_model(by_uuid[uuid])
            for purpose, uuid in stored.items()
            if isinstance(uuid, str) and uuid in by_uuid
        }

    async def site_media_item(self, purpose: str) -> Media | None:
        """The live, publicly viewable media in one slot, or ``None``.

        A stale slot (media since deleted or made private) reads as empty,
        as it does on the public document.
        """
        media_uuid = (await self._document(SITE_MEDIA_KEY)).get(purpose)
        if not isinstance(media_uuid, str):
            return None
        media = (
            await self.db.execute(
                select(Media).where(
                    Media.uuid == media_uuid, Media.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        return media if media is not None and can_view_media(media, None) else None

    async def public_document(self) -> PublicClubInfoResponse:
        """Both documents, ``{}`` where nothing is set (R16)."""
        return PublicClubInfoResponse(
            club_info=await self._document(CLUB_INFO_KEY),
            site_media=await self._site_media(),
        )

    async def branding(self) -> tuple[str, str]:
        """``(club_name, club_short_name)`` from club_info, else the deployment's (R19)."""
        info = await self._document(CLUB_INFO_KEY)
        name = info.get("name") or email_settings.club_name
        short = info.get("shortName") or email_settings.club_short_name
        return str(name), str(short)
