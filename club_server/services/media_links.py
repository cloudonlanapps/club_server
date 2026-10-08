"""Per-owner media link service + cross-owner queries (#162)."""

from typing import Any

from sqlalchemy import distinct, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import USER_AVATAR_TAG
from ..db.models.media import Media
from ..db.models.media_links import (
    EventMediaLink,
    GroupMediaLink,
    UserMediaLink,
    VenueMediaLink,
)
from ..db.models.user import User
from ..exceptions import (
    MediaInUseException,
    MediaLinkExistsException,
    MediaLinkNotFoundException,
    MediaLinkTagFullException,
    MediaLinkTooManyTagsException,
    MediaNotFoundException,
)
from ..db.models.evaluation import EvaluationMediaLink
from ..schemas.media import MediaRef
from ..services.media import can_view_media
from .media_link_owners import (
    deleted_owner_keys,
    is_owner_deleted,
    refuse_write_when_owner_deleted,
)
from ..utils import now_utc_ms


OWNER_REGISTRY: dict[str, tuple[Any, str]] = {
    "user": (UserMediaLink, "username"),
    "event": (EventMediaLink, "event_id"),
    "group": (GroupMediaLink, "group_id"),
    "venue": (VenueMediaLink, "venue_id"),
    "evaluation": (EvaluationMediaLink, "evaluation_id"),
}


def _link_to_response(link, media: Media, owner_deleted: bool = False) -> dict:
    """A link row: what the link says, and the media it points at.

    The media is the same [MediaRef] every other projection embeds, rather
    than the same facts flattened under a second set of names — `mediaUuid`
    for what is `uuid` everywhere else (#426). ``ownerDeleted`` says the
    owner is soft-deleted, which makes the link read-only (#517).
    """
    return {
        "tag": link.tag,
        "metadata": link.metadata_value,
        "media": MediaRef.from_model(media).model_dump(by_alias=True),
        "createdAtUtc": link.created_at,
        "updatedAtUtc": link.updated_at,
        "ownerDeleted": owner_deleted,
    }


class MediaLinkService:
    """CRUD for one owner type's link table."""

    def __init__(self, db: AsyncSession, owner_type: str):
        if owner_type not in OWNER_REGISTRY:
            raise ValueError(f"Unknown owner_type: {owner_type}")
        self.db = db
        self.owner_type = owner_type
        self.model, self.owner_col_name = OWNER_REGISTRY[owner_type]
        self.owner_col = getattr(self.model, self.owner_col_name)

    async def list_grouped(
        self,
        owner_id,
        viewer: User | None,
        media_type: str | None = None,
    ) -> dict[str, list[dict]]:
        q = (
            select(self.model, Media)
            .join(Media, Media.uuid == self.model.media_uuid)
            .where(self.owner_col == owner_id)
            .order_by(self.model.tag, self.model.created_at)
        )
        if media_type:
            q = q.where(Media.media_type == media_type)
        rows = (await self.db.execute(q)).all()
        owner_deleted = await is_owner_deleted(self.db, self.owner_type, owner_id)
        out: dict[str, list[dict]] = {}
        for link, media in rows:
            if not can_view_media(media, viewer):
                continue
            out.setdefault(link.tag, []).append(
                _link_to_response(link, media, owner_deleted)
            )
        return out

    async def list_by_tag(
        self,
        owner_id,
        tag: str,
        viewer: User | None,
        media_type: str | None = None,
    ) -> list[dict]:
        q = (
            select(self.model, Media)
            .join(Media, Media.uuid == self.model.media_uuid)
            .where(self.owner_col == owner_id, self.model.tag == tag)
            .order_by(self.model.created_at)
        )
        if media_type:
            q = q.where(Media.media_type == media_type)
        rows = (await self.db.execute(q)).all()
        owner_deleted = await is_owner_deleted(self.db, self.owner_type, owner_id)
        return [
            _link_to_response(link, media, owner_deleted)
            for link, media in rows
            if can_view_media(media, viewer)
        ]

    async def get(
        self,
        owner_id,
        tag: str,
        media_uuid: str,
        viewer: User | None,
    ) -> dict:
        q = (
            select(self.model, Media)
            .join(Media, Media.uuid == self.model.media_uuid)
            .where(
                self.owner_col == owner_id,
                self.model.tag == tag,
                self.model.media_uuid == media_uuid,
            )
        )
        row = (await self.db.execute(q)).first()
        if row is None:
            raise MediaLinkNotFoundException(
                self.owner_type,
                owner_id,
                tag,
                media_uuid,
            )
        link, media = row
        # Treat invisible-to-viewer as not-found so existence is not disclosed.
        if not can_view_media(media, viewer):
            raise MediaLinkNotFoundException(
                self.owner_type,
                owner_id,
                tag,
                media_uuid,
            )
        owner_deleted = await is_owner_deleted(self.db, self.owner_type, owner_id)
        return _link_to_response(link, media, owner_deleted)

    async def _get_row(self, owner_id, tag: str, media_uuid: str):
        q = select(self.model).where(
            self.owner_col == owner_id,
            self.model.tag == tag,
            self.model.media_uuid == media_uuid,
        )
        return (await self.db.execute(q)).scalar_one_or_none()

    async def create(
        self,
        owner_id,
        tag: str,
        media_uuid: str,
        metadata: str | None,
        viewer: User,
    ) -> dict:
        """Link ``media_uuid`` to the owner under ``tag``.

        Media the caller may not view is answered as missing, so a link
        cannot be used to read it or to probe whether a uuid exists (#463).
        """
        await refuse_write_when_owner_deleted(self.db, self.owner_type, owner_id)
        media = (
            await self.db.execute(
                select(Media).where(
                    Media.uuid == media_uuid,
                    Media.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if media is None or not can_view_media(media, viewer):
            raise MediaNotFoundException(media_uuid)

        existing = await self._get_row(owner_id, tag, media_uuid)
        if existing is not None:
            raise MediaLinkExistsException(
                self.owner_type,
                owner_id,
                tag,
                media_uuid,
            )

        # Limit checks
        tag_count = (
            await self.db.execute(
                select(func.count())
                .select_from(self.model)
                .where(self.owner_col == owner_id, self.model.tag == tag)
            )
        ).scalar_one()
        # A tag that holds one item is never full: linking replaces (#28).
        if tag_count >= settings.media_max_links_per_owner_tag and not self.holds_one(
            tag
        ):
            raise MediaLinkTagFullException(
                self.owner_type,
                owner_id,
                tag,
                settings.media_max_links_per_owner_tag,
            )

        distinct_tags = (
            await self.db.execute(
                select(func.count(distinct(self.model.tag))).where(
                    self.owner_col == owner_id
                )
            )
        ).scalar_one()
        # If the tag is new for this owner, adding it grows the distinct-tag count.
        if tag_count == 0 and distinct_tags >= settings.media_max_tags_per_owner:
            raise MediaLinkTooManyTagsException(
                self.owner_type,
                owner_id,
                settings.media_max_tags_per_owner,
            )

        now = now_utc_ms()
        kwargs: dict[str, Any] = {
            self.owner_col_name: owner_id,
            "media_uuid": media_uuid,
            "tag": tag,
            "metadata_value": metadata,
            "created_at": now,
            "updated_at": now,
        }
        link = self.model(**kwargs)
        self.db.add(link)
        await self.db.flush()
        return _link_to_response(link, media)

    def holds_one(self, tag: str) -> bool:
        """Whether an owner of this type keeps a single item under ``tag``.

        Only a user's avatar does (#28); every other tag keeps what is linked.
        """
        return self.owner_type == "user" and tag == USER_AVATAR_TAG

    async def replace_others(
        self, owner_id: str | int, tag: str, keep_media_uuid: str
    ) -> tuple[list[str], list[str]]:
        """Remove the owner's links under ``tag`` other than the one kept.

        A no-op unless the tag holds one item (``holds_one``). Every other
        link goes, whether or not the caller may view its item: the listing
        a client would work from leaves those out, which is how an old
        avatar used to stay behind (#28). An item no link uses any more is
        soft-deleted; one still linked elsewhere is left as it is.

        Returns the uuids whose link was removed, and those soft-deleted.
        """
        if not self.holds_one(tag):
            return [], []
        others = (
            (
                await self.db.execute(
                    select(self.model)
                    .where(
                        self.owner_col == owner_id,
                        self.model.tag == tag,
                        self.model.media_uuid != keep_media_uuid,
                    )
                    .order_by(self.model.created_at)
                )
            )
            .scalars()
            .all()
        )
        replaced = [link.media_uuid for link in others]
        for link in others:
            await self.db.delete(link)
        await self.db.flush()

        deleted: list[str] = []
        now = now_utc_ms()
        for media_uuid in replaced:
            if await check_media_in_use(self.db, media_uuid):
                continue
            media = (
                await self.db.execute(
                    select(Media).where(
                        Media.uuid == media_uuid, Media.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if media is None:
                continue
            media.deleted_at = now
            media.updated_at = now
            deleted.append(media_uuid)
        await self.db.flush()
        return replaced, deleted

    async def update_metadata(
        self,
        owner_id,
        tag: str,
        media_uuid: str,
        metadata: str | None,
    ) -> tuple[dict, str | None]:
        await refuse_write_when_owner_deleted(self.db, self.owner_type, owner_id)
        link = await self._get_row(owner_id, tag, media_uuid)
        if link is None:
            raise MediaLinkNotFoundException(
                self.owner_type,
                owner_id,
                tag,
                media_uuid,
            )
        old = link.metadata_value
        link.metadata_value = metadata
        link.updated_at = now_utc_ms()
        await self.db.flush()
        media = (
            await self.db.execute(select(Media).where(Media.uuid == media_uuid))
        ).scalar_one()
        return _link_to_response(link, media), old

    async def delete_one(self, owner_id, tag: str, media_uuid: str) -> dict:
        await refuse_write_when_owner_deleted(self.db, self.owner_type, owner_id)
        link = await self._get_row(owner_id, tag, media_uuid)
        if link is None:
            raise MediaLinkNotFoundException(
                self.owner_type,
                owner_id,
                tag,
                media_uuid,
            )
        payload = {
            "owner_type": self.owner_type,
            "owner_id": str(owner_id),
            "tag": link.tag,
            "media_uuid": link.media_uuid,
        }
        await self.db.delete(link)
        await self.db.flush()
        return payload

    async def delete_tag(self, owner_id, tag: str) -> list[str]:
        await refuse_write_when_owner_deleted(self.db, self.owner_type, owner_id)
        rows = (
            (
                await self.db.execute(
                    select(self.model).where(
                        self.owner_col == owner_id,
                        self.model.tag == tag,
                    )
                )
            )
            .scalars()
            .all()
        )
        removed: list[str] = []
        for link in rows:
            removed.append(link.media_uuid)
            await self.db.delete(link)
        await self.db.flush()
        return removed


async def get_media_links(
    db: AsyncSession,
    media_uuid: str,
    viewer: User | None = None,
    *,
    enforce_access: bool = True,
) -> list[dict]:
    """Reverse lookup via the ``media_in_use`` view.

    When ``enforce_access`` is True (the API-facing default), returns []
    if the caller cannot see the media itself (#167). When False (internal
    callers like soft-delete `MEDIA_IN_USE` guard), returns all rows
    irrespective of viewer.
    """
    if enforce_access:
        media = (
            await db.execute(select(Media).where(Media.uuid == media_uuid))
        ).scalar_one_or_none()
        if media is None or not can_view_media(media, viewer):
            return []

    sql = text(
        "SELECT owner_type, owner_id, tag, metadata_value, created_at, updated_at "
        "FROM media_in_use WHERE media_uuid = :uuid "
        "ORDER BY owner_type, tag, created_at"
    )
    rows = (await db.execute(sql, {"uuid": media_uuid})).mappings().all()
    deleted = await deleted_owner_keys(
        db, [(r["owner_type"], r["owner_id"]) for r in rows]
    )
    return [
        {
            "ownerType": r["owner_type"],
            "ownerId": r["owner_id"],
            "tag": r["tag"],
            "metadata": r["metadata_value"],
            "createdAtUtc": r["created_at"],
            "updatedAtUtc": r["updated_at"],
            "ownerDeleted": (r["owner_type"], str(r["owner_id"])) in deleted,
        }
        for r in rows
    ]


async def search_media_links(
    db: AsyncSession,
    viewer: User | None,
    owner_type: str | None = None,
    tag: str | None = None,
    media_type: str | None = None,
    is_encrypted: bool | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """Admin/coach cross-owner search using the view joined to ``media``.

    Rows the ``viewer`` cannot satisfy under ``media.access_roles`` are
    filtered out before pagination (#167). The endpoint guard already
    restricts callers to admin/coach/super-admin; this filter exists so
    a coach (no admin role) cannot see admin-only files.

    ``is_encrypted`` filters by encryption state; the encrypt-backfill sweep
    (#285) uses ``tag=identity_document`` + ``is_encrypted=False`` to enumerate
    the plaintext identity documents that still need encrypting.
    """
    base = (
        "FROM media_in_use v JOIN media m ON m.uuid = v.media_uuid "
        "WHERE m.deleted_at IS NULL"
    )
    params: dict[str, Any] = {}
    if owner_type:
        base += " AND v.owner_type = :owner_type"
        params["owner_type"] = owner_type
    if tag:
        base += " AND v.tag = :tag"
        params["tag"] = tag
    if media_type:
        base += " AND m.media_type = :media_type"
        params["media_type"] = media_type
    if is_encrypted is not None:
        base += " AND m.is_encrypted = :is_encrypted"
        params["is_encrypted"] = 1 if is_encrypted else 0

    rows = (
        (
            await db.execute(
                text(
                    "SELECT v.media_uuid, v.owner_type, v.owner_id, v.tag, "
                    "v.metadata_value, m.media_type, m.access_roles, m.uploaded_by, "
                    "m.is_encrypted, v.created_at "
                    f"{base} ORDER BY v.created_at DESC"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    # Filter in Python: the predicate spans uploader-identity + caller roles,
    # which is awkward to express in SQL and the link tables are small.
    class _MediaShim:
        def __init__(self, access_roles, uploaded_by):
            self.access_roles = access_roles
            self.uploaded_by = uploaded_by

    visible = [
        r
        for r in rows
        if can_view_media(_MediaShim(r["access_roles"], r["uploaded_by"]), viewer)
    ]
    total = len(visible)
    page = visible[offset : offset + limit]
    deleted = await deleted_owner_keys(
        db, [(r["owner_type"], r["owner_id"]) for r in page]
    )

    items = [
        {
            "mediaUuid": r["media_uuid"],
            "ownerType": r["owner_type"],
            "ownerId": r["owner_id"],
            "tag": r["tag"],
            "metadata": r["metadata_value"],
            "mediaType": r["media_type"],
            "isEncrypted": bool(r["is_encrypted"]),
            "createdAtUtc": r["created_at"],
            "ownerDeleted": (r["owner_type"], str(r["owner_id"])) in deleted,
        }
        for r in page
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


async def check_media_in_use(
    db: AsyncSession,
    media_uuid: str,
) -> list[dict]:
    """Return the link list referencing ``media_uuid``. Empty = safe to delete.

    Internal: bypasses the per-viewer visibility filter (#167); used by the
    soft-delete guard, which must see all references regardless of caller.
    """
    return await get_media_links(db, media_uuid, enforce_access=False)


async def assert_media_not_in_use(db: AsyncSession, media_uuid: str) -> None:
    links = await check_media_in_use(db, media_uuid)
    if links:
        raise MediaInUseException(media_uuid, links)
