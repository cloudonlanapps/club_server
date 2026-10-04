"""Router for v2 media endpoints (#161). Parallel to ``routers.uploads``."""

import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.media import Media
from ..db.models.user import User
from ..utils import get_client_ip
from ..dependencies import (
    get_authenticated_user,
    get_db,
    is_admin_or_coach,
    require_admin_or_coach,
    require_super_admin,
)
from ..exceptions import (
    DecryptionFailedException,
    EncryptedFileTooLargeException,
    EncryptionNotConfiguredException,
    EncryptionNotSupportedForVideoException,
    FileTooLargeException,
    InvalidAccessRolesException,
    InvalidMediaTypeException,
    MediaFileMissingException,
    MediaInUseException,
)
from ..schemas.common import PaginatedResponse
from ..schemas.media import MediaPatchRequest, MediaResponse
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.auth import AuthService
from ..services.media import (
    MediaService,
    allowed_variants,
    can_modify_media,
    can_view_media,
    resolve_media_file_path,
)
from ..services.media_links import (
    OWNER_REGISTRY,
    assert_media_not_in_use,
    get_media_links,
    search_media_links,
)

router = APIRouter(prefix="/media", tags=["Media (v2)"])

# A plain download may be kept for a day. Only media anonymous callers may
# view is marked cacheable by shared caches; anything restricted by its
# access roles is for the requesting browser alone (#504).
_PUBLIC_DOWNLOAD_CACHE = "public, max-age=86400"
_PRIVATE_DOWNLOAD_CACHE = "private, max-age=86400"
_ENCRYPTED_DOWNLOAD_CACHE = "private, no-store"


def _media_404() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "MEDIA_NOT_FOUND", "message": "Media not found"},
    )


def _require_media_modify(media: Media, user: User) -> None:
    """Refuse a caller who may not mutate ``media`` (#503).

    One who can view the item is told it is forbidden (403); one who
    cannot is answered as if it did not exist (404).
    """
    if can_modify_media(media, user):
        return
    if can_view_media(media, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INSUFFICIENT_PERMISSION",
                "message": "Only the uploader or an admin can change this media",
            },
        )
    raise _media_404()


def _parse_access_roles(raw: str | None) -> list[str] | None:
    """Parse the ``accessRoles`` form field.

    Returns the decoded list, or ``None`` when the field is absent. Raises
    ``InvalidAccessRolesException`` (caught by the same handlers as the
    service-side validator in ``media_pipeline``) on malformed input.
    """
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        raise InvalidAccessRolesException("accessRoles must be a JSON array of strings")
    if not isinstance(decoded, list):
        raise InvalidAccessRolesException("accessRoles must be a JSON array of strings")
    return decoded


@router.get("", response_model=PaginatedResponse[MediaResponse])
async def list_media(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    media_type: Annotated[str | None, Query(alias="mediaType")] = None,
    conversion_status: Annotated[str | None, Query(alias="conversionStatus")] = None,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
):
    """List media records (admin/coach only)."""
    return await MediaService(db).list_media(
        offset=offset,
        limit=limit,
        media_type=media_type,
        conversion_status=conversion_status,
        include_deleted=include_deleted,
    )


@router.get("/myfiles", response_model=PaginatedResponse[MediaResponse])
async def list_my_media(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    media_type: Annotated[str | None, Query(alias="mediaType")] = None,
    conversion_status: Annotated[str | None, Query(alias="conversionStatus")] = None,
):
    """List the caller's own media."""
    return await MediaService(db).list_user_media(
        uploaded_by=current_user.username,
        offset=offset,
        limit=limit,
        media_type=media_type,
        conversion_status=conversion_status,
    )


@router.post("")
async def upload_media(
    request: Request,
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    preserve_original: Annotated[bool, Form(alias="preserveOriginal")] = False,
    duration: Annotated[float | None, Form()] = None,
    start: Annotated[float | None, Form()] = None,
    access_roles: Annotated[str | None, Form(alias="accessRoles")] = None,
    encrypt: Annotated[bool, Form()] = False,
):
    """Upload a media file. Returns 201 for image/pdf (sync), 202 for video (async)."""
    media_service = MediaService(db)
    audit_service = AuditService(db)

    try:
        media = await media_service.create_media(
            file=file,
            uploaded_by=current_user.username,
            preserve_original=preserve_original,
            duration=duration,
            start=start,
            access_roles=_parse_access_roles(access_roles),
            encrypt=encrypt,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.UPLOAD_MEDIA_V2,
            resource_type="media",
            resource_id=str(media.id),
            details={
                "filename": media.original_filename,
                "media_type": media.media_type,
                "preserve_original": preserve_original,
            },
            ip_address=get_client_ip(request),
        )

        response_data = MediaResponse.from_model(media).model_dump(by_alias=True)
        if media.media_type == "video":
            return JSONResponse(content=response_data, status_code=202)
        return JSONResponse(content=response_data, status_code=201)

    except InvalidMediaTypeException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_MEDIA_TYPE", "message": str(e)},
        )
    except EncryptionNotSupportedForVideoException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "ENCRYPTION_NOT_SUPPORTED_FOR_VIDEO", "message": str(e)},
        )
    except EncryptedFileTooLargeException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "ENCRYPTED_FILE_TOO_LARGE",
                "message": str(e),
                "limitMb": e.max_size_mb,
            },
        )
    except EncryptionNotConfiguredException as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ENCRYPTION_NOT_CONFIGURED", "message": str(e)},
        )
    except InvalidAccessRolesException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_ACCESS_ROLES", "message": str(e)},
        )
    except FileTooLargeException as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": str(e),
                "mediaType": e.media_type,
                "limitMb": e.max_size_mb,
            },
        )


@router.get("/by_id/{media_id}", response_model=MediaResponse)
async def get_media(
    media_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Get media metadata by ID. Owner or admin/coach only; otherwise 404."""
    service = MediaService(db)
    media = await service.get(media_id, include_deleted=True)

    if media.uploaded_by != current_user.username and not is_admin_or_coach(
        current_user
    ):
        raise _media_404()
    return MediaResponse.from_model(media)


@router.patch("/by_id/{media_id}", response_model=MediaResponse)
async def patch_media(
    request: Request,
    media_id: int,
    data: MediaPatchRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Update access roles. Uploader or admin only (#503)."""
    service = MediaService(db)
    audit_service = AuditService(db)
    media = await service.get(media_id)
    _require_media_modify(media, current_user)

    if "access_roles" in data.model_fields_set and data.access_roles is not None:
        try:
            media, old_roles = await service.update_access_roles(
                media_id,
                data.access_roles,
            )
        except InvalidAccessRolesException as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_ACCESS_ROLES", "message": str(e)},
            )
        new_roles = json.loads(media.access_roles)
        if old_roles != new_roles:
            await audit_service.log(
                actor_username=current_user.username,
                action=AuditAction.UPDATE_MEDIA_V2,
                resource_type="media",
                resource_id=str(media_id),
                details={"access_roles": {"old": old_roles, "new": new_roles}},
                ip_address=get_client_ip(request),
            )

    return MediaResponse.from_model(media)


@router.post("/by_id/{uuid}/encrypt", response_model=MediaResponse)
async def encrypt_media(
    request: Request,
    uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Encrypt an existing plaintext media artifact in place. Super admin only.

    Single-direction (no decrypt) and idempotent: re-invoking on an
    already-encrypted row is a 200 no-op. Drives the identity-document
    encryption backfill (#285).
    """
    service = MediaService(db)
    audit_service = AuditService(db)

    try:
        media, did_encrypt = await service.encrypt_in_place(uuid)
    except EncryptionNotSupportedForVideoException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "ENCRYPTION_NOT_SUPPORTED_FOR_VIDEO", "message": str(e)},
        )
    except EncryptionNotConfiguredException as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ENCRYPTION_NOT_CONFIGURED", "message": str(e)},
        )
    except MediaFileMissingException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": str(e)},
        )

    if did_encrypt:
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ENCRYPT_MEDIA_V2,
            resource_type="media",
            resource_id=str(media.id),
            details={
                "filename": media.original_filename,
                "media_type": media.media_type,
            },
            ip_address=get_client_ip(request),
        )

    return MediaResponse.from_model(media)


async def _resolve_optional_user(request: Request, db: AsyncSession) -> User | None:
    """Best-effort token resolver: returns user or None (used by download)."""
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization",
    )
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    auth_service = AuthService(db)
    payload = auth_service.decode_token(token)
    if payload is None:
        return None
    user = await auth_service.get_user_by_username(payload.sub)
    if user is None:
        return None
    if AuthService.issued_before_password_change(payload, user):
        return None
    if user.status in ("blocked", "left"):
        return None
    return user


def _enforce_download_guard(media, viewer: User | None) -> None:
    if can_view_media(media, viewer):
        return
    if viewer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTHENTICATION_REQUIRED",
                "message": "This file requires authentication",
            },
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": "You do not have access to this file"},
    )


# Four single-method registrations rather than two two-method ones: FastAPI
# derives an operation id per *route*, so a route carrying both GET and HEAD
# gives the two operations the same id and any generated client breaks on it.
@router.get("/by_id/{uuid}/download")
@router.head("/by_id/{uuid}/download")
@router.get("/by_id/{uuid}/download/{filename:path}")
@router.head("/by_id/{uuid}/download/{filename:path}")
async def download_media(
    uuid: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    variant: Annotated[str, Query()] = "original",
    filename: str | None = None,
):
    """Download a media artifact by UUID.

    Variants by media_type:
    - image: ``original``
    - video: ``original``, ``poster``, ``animated``
    - pdf:   ``original``, ``poster``

    [filename] is decorative and ignored (#424). The lookup is by uuid alone,
    so there is nothing to validate and no way for a wrong name to become an
    error — but the URL ends in a real file extension, which is what a client
    reads to tell a video from a picture, and what a browser saves the file
    under. `MediaRef.filename` is the name to use.

    HEAD is answered as well as GET: a client that only wants the type should
    not have to fetch a byte of a 44MB video to get it.
    """
    service = MediaService(db)
    media = await service.get_by_uuid(uuid)

    if media.conversion_status in ("pending", "processing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONVERSION_IN_PROGRESS",
                "message": "File is still being converted",
            },
        )
    if media.conversion_status == "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "CONVERSION_FAILED", "message": "File conversion failed"},
        )

    if variant not in allowed_variants(media.media_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_VARIANT",
                "message": (
                    f"Variant '{variant}' not valid for {media.media_type}; "
                    f"allowed: {allowed_variants(media.media_type)}"
                ),
            },
        )

    viewer = await _resolve_optional_user(request, db)
    _enforce_download_guard(media, viewer)

    file_path = resolve_media_file_path(media, variant)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": "File not found on disk"},
        )

    try:
        result = service.resolve_download(media, file_path)
    except EncryptionNotConfiguredException as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ENCRYPTION_NOT_CONFIGURED", "message": str(e)},
        )
    except DecryptionFailedException as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DECRYPTION_FAILED", "message": str(e)},
        )

    if result.content is not None:
        return Response(
            content=result.content,
            media_type=result.media_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Cache-Control": _ENCRYPTED_DOWNLOAD_CACHE,
            },
        )

    cache_control = (
        _PUBLIC_DOWNLOAD_CACHE
        if can_view_media(media, None)
        else _PRIVATE_DOWNLOAD_CACHE
    )
    return FileResponse(
        path=str(result.file_path),
        media_type=result.media_type,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Cache-Control": cache_control,
        },
    )


@router.get("/links")
async def cross_owner_search_links(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    owner_type: Annotated[str | None, Query(alias="ownerType")] = None,
    tag: Annotated[str | None, Query()] = None,
    media_type: Annotated[str | None, Query(alias="mediaType")] = None,
    is_encrypted: Annotated[bool | None, Query(alias="isEncrypted")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Cross-owner search over ``media_in_use`` (admin/coach)."""
    # Every owner the link registry knows, evaluation included, whether or not
    # the evaluations module is on: leftover evaluation media must stay
    # findable so it can be removed (#489).
    if owner_type is not None and owner_type not in OWNER_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_OWNER_TYPE",
                "message": "ownerType must be one of " + "|".join(OWNER_REGISTRY),
            },
        )
    return await search_media_links(
        db,
        viewer=current_user,
        owner_type=owner_type,
        tag=tag,
        media_type=media_type,
        is_encrypted=is_encrypted,
        offset=offset,
        limit=limit,
    )


@router.get("/by_id/{uuid}/links", response_model=list[dict])
async def list_media_links(
    uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Reverse lookup: every link row referencing this media."""
    service = MediaService(db)
    _ = await service.get_by_uuid(uuid, include_deleted=True)
    return await get_media_links(db, uuid, viewer=current_user)


@router.delete("/by_id/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_media(
    request: Request,
    media_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Soft-delete a media record. Uploader or admin only. Files stay on disk."""
    service = MediaService(db)
    audit_service = AuditService(db)
    media = await service.get(media_id)
    _require_media_modify(media, current_user)

    try:
        await assert_media_not_in_use(db, media.uuid)
    except MediaInUseException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MEDIA_IN_USE",
                "message": str(e),
                "links": e.links,
            },
        )

    await service.soft_delete(media_id)
    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.SOFT_DELETE_MEDIA_V2,
        resource_type="media",
        resource_id=str(media_id),
        details={
            "filename": media.original_filename,
            "media_type": media.media_type,
        },
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/by_id/{media_id}/restore", response_model=MediaResponse)
async def restore_media(
    request: Request,
    media_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
):
    """Restore a soft-deleted media record. Uploader or admin only."""
    service = MediaService(db)
    audit_service = AuditService(db)
    media = await service.get(media_id, include_deleted=True)
    _require_media_modify(media, current_user)

    media = await service.restore(media_id)
    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.RESTORE_MEDIA_V2,
        resource_type="media",
        resource_id=str(media_id),
        details={},
        ip_address=get_client_ip(request),
    )
    return MediaResponse.from_model(media)


@router.delete("/by_id/{media_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_media(
    request: Request,
    media_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Permanently delete a soft-deleted media record. Super admin only."""
    service = MediaService(db)
    audit_service = AuditService(db)
    media = await service.get(media_id, include_deleted=True)

    await service.hard_delete(media_id)

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.HARD_DELETE_MEDIA_V2,
        resource_type="media",
        resource_id=str(media_id),
        details={
            "filename": media.original_filename,
            "media_type": media.media_type,
        },
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
