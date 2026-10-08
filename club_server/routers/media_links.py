"""Per-owner media link routers (#162). Four routers (user, event, group,
venue) share the same handlers via a small dispatch helper.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.event import Event
from ..db.models.group import Group
from ..db.models.user import User
from ..db.models.venue import Venue
from ..dependencies import (
    get_authenticated_user,
    get_db,
    is_admin,
    is_admin_or_coach,
    require_admin_or_coach,
)
from ..exceptions import (
    MediaLinkExistsException,
    MediaLinkTagFullException,
    MediaLinkTooManyTagsException,
    MediaNotFoundException,
)
from ..schemas.common import FieldValue
from ..schemas.media_links import MediaLinkCreate, MediaLinkPatch
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.media_links import MediaLinkService
from ..utils import get_client_ip

TAG_PATH_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

EnsureFn = Callable[[AsyncSession, Any], Awaitable[None]]


# ---------- helpers ----------


def _link_404() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "MEDIA_LINK_NOT_FOUND", "message": "Media link not found"},
    )


def _owner_404(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": code, "message": message},
    )


async def _ensure_user(db: AsyncSession, username: str) -> None:
    if (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none() is None:
        raise _owner_404("USER_NOT_FOUND", "User not found")


async def _ensure_event(db: AsyncSession, event_id: int) -> None:
    """A soft-deleted event still has its links; writes are refused later (#517)."""
    if (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalar_one_or_none() is None:
        raise _owner_404("EVENT_NOT_FOUND", "Event not found")


async def _ensure_group(db: AsyncSession, group_id: int) -> None:
    if (
        await db.execute(select(Group).where(Group.id == group_id))
    ).scalar_one_or_none() is None:
        raise _owner_404("GROUP_NOT_FOUND", "Group not found")


async def _ensure_venue(db: AsyncSession, venue_id: int) -> None:
    if (
        await db.execute(select(Venue).where(Venue.id == venue_id))
    ).scalar_one_or_none() is None:
        raise _owner_404("VENUE_NOT_FOUND", "Venue not found")


def _map_create_errors(
    e: Exception,
    owner_type: str,
    owner_id: object,
) -> HTTPException:
    if isinstance(e, MediaNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MEDIA_NOT_FOUND", "message": "Media not found"},
        )
    if isinstance(e, MediaLinkExistsException):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MEDIA_LINK_EXISTS",
                "message": "Link with this (tag, mediaUuid) already exists",
            },
        )
    if isinstance(e, MediaLinkTagFullException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MEDIA_LINK_TAG_FULL",
                "message": f"Tag {e.tag} already has the maximum {e.limit} links",
            },
        )
    if isinstance(e, MediaLinkTooManyTagsException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MEDIA_LINK_TOO_MANY_TAGS",
                "message": f"Already at the maximum {e.limit} distinct media tags",
            },
        )
    raise e


async def _audit_create(
    db: AsyncSession,
    actor: str,
    owner_type: str,
    owner_id: object,
    tag: str,
    media_uuid: str,
    metadata: str | None,
    target_username: str | None = None,
    ip_address: str | None = None,
    replaced: list[str] | None = None,
    deleted: list[str] | None = None,
) -> None:
    """Audit a new link. ``replaced`` names the items whose link it took the
    place of, and ``deleted`` those of them that were soft-deleted (#28)."""
    details: dict[str, FieldValue] = {
        "ownerType": owner_type,
        "ownerId": str(owner_id),
        "tag": tag,
        "mediaUuid": media_uuid,
        "metadata": metadata,
    }
    if replaced:
        details["replacedMediaUuids"] = replaced
        details["deletedMediaUuids"] = deleted or []
    await AuditService(db).log(
        actor_username=actor,
        action=AuditAction(f"create_{owner_type}_media_link"),
        target_username=target_username,
        ip_address=ip_address,
        resource_type=f"{owner_type}_media_link",
        resource_id=f"{owner_id}:{tag}:{media_uuid}",
        details=details,
    )


async def _audit_update(
    db: AsyncSession,
    actor: str,
    owner_type: str,
    owner_id: object,
    tag: str,
    media_uuid: str,
    old: str | None,
    new: str | None,
    target_username: str | None = None,
    ip_address: str | None = None,
) -> None:
    await AuditService(db).log(
        actor_username=actor,
        action=AuditAction(f"update_{owner_type}_media_link"),
        target_username=target_username,
        ip_address=ip_address,
        resource_type=f"{owner_type}_media_link",
        resource_id=f"{owner_id}:{tag}:{media_uuid}",
        details={
            "ownerType": owner_type,
            "ownerId": str(owner_id),
            "metadata": {"old": old, "new": new},
        },
    )


async def _audit_delete_one(
    db: AsyncSession,
    actor: str,
    owner_type: str,
    owner_id: object,
    tag: str,
    media_uuid: str,
    target_username: str | None = None,
    ip_address: str | None = None,
) -> None:
    await AuditService(db).log(
        actor_username=actor,
        action=AuditAction(f"delete_{owner_type}_media_link"),
        target_username=target_username,
        ip_address=ip_address,
        resource_type=f"{owner_type}_media_link",
        resource_id=f"{owner_id}:{tag}:{media_uuid}",
        details={
            "ownerType": owner_type,
            "ownerId": str(owner_id),
            "tag": tag,
            "mediaUuid": media_uuid,
        },
    )


async def _audit_delete_tag(
    db: AsyncSession,
    actor: str,
    owner_type: str,
    owner_id: object,
    tag: str,
    removed: list[str],
    target_username: str | None = None,
    ip_address: str | None = None,
) -> None:
    await AuditService(db).log(
        actor_username=actor,
        action=AuditAction(f"delete_{owner_type}_media_tag"),
        target_username=target_username,
        ip_address=ip_address,
        resource_type=f"{owner_type}_media_tag",
        resource_id=f"{owner_id}:{tag}",
        details={
            "ownerType": owner_type,
            "ownerId": str(owner_id),
            "tag": tag,
            "removedMediaUuids": removed,
        },
    )


# ---------- shared handler bodies ----------


async def _handle_list_grouped(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
):
    await ensure_fn(db, owner_id)
    return await MediaLinkService(db, owner_type).list_grouped(owner_id, current_user)


async def _handle_list_by_tag(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
    tag: str,
):
    await ensure_fn(db, owner_id)
    return await MediaLinkService(db, owner_type).list_by_tag(
        owner_id,
        tag,
        current_user,
    )


async def _handle_get_one(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
    tag: str,
    media_uuid: str,
):
    await ensure_fn(db, owner_id)
    return await MediaLinkService(db, owner_type).get(
        owner_id,
        tag,
        media_uuid,
        current_user,
    )


async def _handle_create(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
    body: MediaLinkCreate,
    target_username: str | None = None,
    ip_address: str | None = None,
):
    await ensure_fn(db, owner_id)
    service = MediaLinkService(db, owner_type)
    try:
        payload = await service.create(
            owner_id,
            body.tag,
            body.media_uuid,
            body.metadata,
            viewer=current_user,
        )
    except Exception as e:
        raise _map_create_errors(e, owner_type, owner_id)
    # A user has one avatar: the new link takes the place of the others (#28).
    replaced, deleted = await service.replace_others(
        owner_id, body.tag, body.media_uuid
    )
    await _audit_create(
        db,
        current_user.username,
        owner_type,
        owner_id,
        body.tag,
        body.media_uuid,
        body.metadata,
        target_username=target_username,
        ip_address=ip_address,
        replaced=replaced,
        deleted=deleted,
    )
    return payload


async def _handle_patch(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
    tag: str,
    media_uuid: str,
    body: MediaLinkPatch,
    target_username: str | None = None,
    ip_address: str | None = None,
):
    await ensure_fn(db, owner_id)
    payload, old = await MediaLinkService(db, owner_type).update_metadata(
        owner_id,
        tag,
        media_uuid,
        body.metadata,
    )
    await _audit_update(
        db,
        current_user.username,
        owner_type,
        owner_id,
        tag,
        media_uuid,
        old,
        body.metadata,
        target_username=target_username,
        ip_address=ip_address,
    )
    return payload


async def _handle_delete_one(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
    tag: str,
    media_uuid: str,
    target_username: str | None = None,
    ip_address: str | None = None,
):
    await ensure_fn(db, owner_id)
    await MediaLinkService(db, owner_type).delete_one(owner_id, tag, media_uuid)
    await _audit_delete_one(
        db,
        current_user.username,
        owner_type,
        owner_id,
        tag,
        media_uuid,
        target_username=target_username,
        ip_address=ip_address,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _handle_delete_tag(
    db: AsyncSession,
    current_user: User,
    owner_type: str,
    owner_id: Any,
    ensure_fn: EnsureFn,
    tag: str,
    target_username: str | None = None,
    ip_address: str | None = None,
):
    await ensure_fn(db, owner_id)
    removed = await MediaLinkService(db, owner_type).delete_tag(owner_id, tag)
    await _audit_delete_tag(
        db,
        current_user.username,
        owner_type,
        owner_id,
        tag,
        removed,
        target_username=target_username,
        ip_address=ip_address,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- /users/by_id/{username}/media ----------

user_media_router = APIRouter(
    prefix="/users/by_id/{username}/media",
    tags=["User Media (v2)"],
)


def _user_read_guard(username: str, current_user: User) -> None:
    if current_user.username == username or is_admin_or_coach(current_user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "INSUFFICIENT_PERMISSION",
            "message": "Cannot view another user's media",
        },
    )


def _user_write_guard(username: str, current_user: User) -> None:
    if current_user.username == username or is_admin(current_user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "INSUFFICIENT_PERMISSION",
            "message": "Cannot modify another user's media",
        },
    )


@user_media_router.get("")
async def user_list_grouped(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    _user_read_guard(username, current_user)
    return await _handle_list_grouped(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
    )


@user_media_router.get("/{tag}")
async def user_list_tag(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    _user_read_guard(username, current_user)
    return await _handle_list_by_tag(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
        tag,
    )


@user_media_router.get("/{tag}/{media_uuid}")
async def user_get_one(
    username: str,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    _user_read_guard(username, current_user)
    return await _handle_get_one(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
        tag,
        media_uuid,
    )


@user_media_router.post("", status_code=status.HTTP_201_CREATED)
async def user_create(
    request: Request,
    username: str,
    body: MediaLinkCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    _user_write_guard(username, current_user)
    return await _handle_create(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
        body,
        target_username=username,
        ip_address=get_client_ip(request),
    )


@user_media_router.patch("/{tag}/{media_uuid}")
async def user_patch(
    request: Request,
    username: str,
    media_uuid: str,
    body: MediaLinkPatch,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    _user_write_guard(username, current_user)
    return await _handle_patch(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
        tag,
        media_uuid,
        body,
        target_username=username,
        ip_address=get_client_ip(request),
    )


@user_media_router.delete("/{tag}/{media_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def user_delete_one(
    request: Request,
    username: str,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    _user_write_guard(username, current_user)
    return await _handle_delete_one(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
        tag,
        media_uuid,
        target_username=username,
        ip_address=get_client_ip(request),
    )


@user_media_router.delete("/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def user_delete_tag(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    _user_write_guard(username, current_user)
    return await _handle_delete_tag(
        db,
        current_user,
        "user",
        username,
        _ensure_user,
        tag,
        target_username=username,
        ip_address=get_client_ip(request),
    )


# ---------- /events/by_id/{event_id}/media ----------

event_media_router = APIRouter(
    prefix="/events/by_id/{event_id}/media",
    tags=["Event Media (v2)"],
)


@event_media_router.get("")
async def event_list_grouped(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    return await _handle_list_grouped(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
    )


@event_media_router.get("/{tag}")
async def event_list_tag(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_list_by_tag(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
        tag,
    )


@event_media_router.get("/{tag}/{media_uuid}")
async def event_get_one(
    event_id: int,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_get_one(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
        tag,
        media_uuid,
    )


@event_media_router.post("", status_code=status.HTTP_201_CREATED)
async def event_create(
    request: Request,
    event_id: int,
    body: MediaLinkCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    return await _handle_create(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
        body,
        ip_address=get_client_ip(request),
    )


@event_media_router.patch("/{tag}/{media_uuid}")
async def event_patch(
    request: Request,
    event_id: int,
    media_uuid: str,
    body: MediaLinkPatch,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_patch(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
        tag,
        media_uuid,
        body,
        ip_address=get_client_ip(request),
    )


@event_media_router.delete(
    "/{tag}/{media_uuid}", status_code=status.HTTP_204_NO_CONTENT
)
async def event_delete_one(
    request: Request,
    event_id: int,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_delete_one(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
        tag,
        media_uuid,
        ip_address=get_client_ip(request),
    )


@event_media_router.delete("/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def event_delete_tag(
    request: Request,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_delete_tag(
        db,
        current_user,
        "event",
        event_id,
        _ensure_event,
        tag,
        ip_address=get_client_ip(request),
    )


# ---------- /groups/by_id/{group_id}/media ----------

group_media_router = APIRouter(
    prefix="/groups/by_id/{group_id}/media",
    tags=["Group Media (v2)"],
)


@group_media_router.get("")
async def group_list_grouped(
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    return await _handle_list_grouped(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
    )


@group_media_router.get("/{tag}")
async def group_list_tag(
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_list_by_tag(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
        tag,
    )


@group_media_router.get("/{tag}/{media_uuid}")
async def group_get_one(
    group_id: int,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_get_one(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
        tag,
        media_uuid,
    )


@group_media_router.post("", status_code=status.HTTP_201_CREATED)
async def group_create(
    request: Request,
    group_id: int,
    body: MediaLinkCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    return await _handle_create(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
        body,
        ip_address=get_client_ip(request),
    )


@group_media_router.patch("/{tag}/{media_uuid}")
async def group_patch(
    request: Request,
    group_id: int,
    media_uuid: str,
    body: MediaLinkPatch,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_patch(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
        tag,
        media_uuid,
        body,
        ip_address=get_client_ip(request),
    )


@group_media_router.delete(
    "/{tag}/{media_uuid}", status_code=status.HTTP_204_NO_CONTENT
)
async def group_delete_one(
    request: Request,
    group_id: int,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_delete_one(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
        tag,
        media_uuid,
        ip_address=get_client_ip(request),
    )


@group_media_router.delete("/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def group_delete_tag(
    request: Request,
    group_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_delete_tag(
        db,
        current_user,
        "group",
        group_id,
        _ensure_group,
        tag,
        ip_address=get_client_ip(request),
    )


# ---------- /venues/by_id/{venue_id}/media ----------

venue_media_router = APIRouter(
    prefix="/venues/by_id/{venue_id}/media",
    tags=["Venue Media (v2)"],
)


@venue_media_router.get("")
async def venue_list_grouped(
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    return await _handle_list_grouped(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
    )


@venue_media_router.get("/{tag}")
async def venue_list_tag(
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_list_by_tag(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
        tag,
    )


@venue_media_router.get("/{tag}/{media_uuid}")
async def venue_get_one(
    venue_id: int,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_get_one(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
        tag,
        media_uuid,
    )


@venue_media_router.post("", status_code=status.HTTP_201_CREATED)
async def venue_create(
    request: Request,
    venue_id: int,
    body: MediaLinkCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    return await _handle_create(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
        body,
        ip_address=get_client_ip(request),
    )


@venue_media_router.patch("/{tag}/{media_uuid}")
async def venue_patch(
    request: Request,
    venue_id: int,
    media_uuid: str,
    body: MediaLinkPatch,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_patch(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
        tag,
        media_uuid,
        body,
        ip_address=get_client_ip(request),
    )


@venue_media_router.delete(
    "/{tag}/{media_uuid}", status_code=status.HTTP_204_NO_CONTENT
)
async def venue_delete_one(
    request: Request,
    venue_id: int,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_delete_one(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
        tag,
        media_uuid,
        ip_address=get_client_ip(request),
    )


@venue_media_router.delete("/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def venue_delete_tag(
    request: Request,
    venue_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    tag: str = Path(..., pattern=TAG_PATH_PATTERN),
):
    return await _handle_delete_tag(
        db,
        current_user,
        "venue",
        venue_id,
        _ensure_venue,
        tag,
        ip_address=get_client_ip(request),
    )
