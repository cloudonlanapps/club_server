"""Application state moves, duplicate, preview and forms (#93).

The four moves of the lifecycle (save, publish, unpublish, revert) and
duplicate are an admin's, as is the preview drawn for the demo member. A
member downloads their own form of a published application; an admin
downloads any member's. Every PDF is drawn per request, never stored.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_authenticated_user,
    get_db,
    require_admin,
    require_applications_enabled,
)
from ..schemas.application import ApplicationResponse
from ..services.application import ApplicationService, application_response
from ..services.application_downloads import ApplicationDownloadService
from ..services.application_rules import PREVIEW_MEMBER
from . import application_errors as err

PDF_MEDIA_TYPE = "application/pdf"
PDF_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {PDF_MEDIA_TYPE: {}}}
}

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(require_applications_enabled)],
)


def pdf_response(content: bytes, application_id: int, username: str) -> Response:
    """A form as ``application-<id>-<username>.pdf``."""
    return Response(
        content=content,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'inline; filename="application-{application_id}-{username}.pdf"'
            )
        },
    )


async def _move(
    db: AsyncSession, application_id: int, action: str
) -> ApplicationResponse:
    try:
        application = await ApplicationService(db).move(application_id, action)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(application)


@router.post("/{application_id}/save", response_model=ApplicationResponse)
async def save_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Draft → saved: checked complete, the layout decided and stored (R13)."""
    return await _move(db, application_id, "save")


@router.post("/{application_id}/publish", response_model=ApplicationResponse)
async def publish_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Saved → published: members see it (R12)."""
    return await _move(db, application_id, "publish")


@router.post("/{application_id}/unpublish", response_model=ApplicationResponse)
async def unpublish_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Published → saved: members no longer see it (R12)."""
    return await _move(db, application_id, "unpublish")


@router.post("/{application_id}/revert", response_model=ApplicationResponse)
async def revert_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Saved → draft: editable again; the stored layout is dropped (R12)."""
    return await _move(db, application_id, "revert")


@router.post(
    "/{application_id}/duplicate",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """A new draft copied from any template (R5)."""
    try:
        copy = await ApplicationService(db).duplicate(
            application_id, current_user.username
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(copy)


@router.get(
    "/{application_id}/preview", response_class=Response, responses=PDF_RESPONSES
)
async def preview_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> Response:
    """The demo member's form in any state, watermarked "PREVIEW" (R19)."""
    try:
        content = await ApplicationDownloadService(db).preview(application_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return pdf_response(content, application_id, PREVIEW_MEMBER["username"])


@router.get("/{application_id}/form", response_class=Response, responses=PDF_RESPONSES)
async def own_form(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> Response:
    """The signed-in member's own form of a published application (R10, R11)."""
    try:
        content = await ApplicationDownloadService(db).own_form(
            current_user, application_id
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return pdf_response(content, application_id, current_user.username)


@router.get(
    "/{application_id}/form/{username}",
    response_class=Response,
    responses=PDF_RESPONSES,
)
async def member_form(
    application_id: int,
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> Response:
    """An admin's download of any member's form of a published application (R20)."""
    try:
        content = await ApplicationDownloadService(db).member_form(
            application_id, username
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return pdf_response(content, application_id, username)
