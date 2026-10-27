"""Application state moves, duplicate, preview and forms (#93).

The four moves of the lifecycle (save, publish, unpublish, revert) and
duplicate are an admin's, as are the preview drawn for the demo member and
the blank form for filling in by hand. A member downloads their own form of
a published application; an admin downloads any member's. A published
template's form is drawn once and filled per download (R25).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
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
from ..utils import get_client_ip
from . import application_errors as err

PDF_MEDIA_TYPE = "application/pdf"
# The file name's last part of a blank form: application-<id>-blank.pdf (R27).
BLANK_FORM = "blank"
CategoryChoice = Annotated[
    int | None,
    Query(
        ge=0,
        description="The age category to tick, by index; needed when the "
        "member fits several (R11a).",
    ),
]
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
    db: AsyncSession, application_id: int, action: str, actor: User, request: Request
) -> ApplicationResponse:
    try:
        application = await ApplicationService(db).move(
            application_id, action, actor.username, get_client_ip(request)
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(application)


@router.post("/{application_id}/save", response_model=ApplicationResponse)
async def save_application(
    request: Request,
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Draft → saved: checked complete, the layout decided and stored (R13)."""
    return await _move(db, application_id, "save", current_user, request)


@router.post("/{application_id}/publish", response_model=ApplicationResponse)
async def publish_application(
    request: Request,
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Saved → published: members see it, under a new publish ID (R12, R22)."""
    return await _move(db, application_id, "publish", current_user, request)


@router.post("/{application_id}/unpublish", response_model=ApplicationResponse)
async def unpublish_application(
    request: Request,
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Published → saved: members no longer see it (R12)."""
    return await _move(db, application_id, "unpublish", current_user, request)


@router.post("/{application_id}/revert", response_model=ApplicationResponse)
async def revert_application(
    request: Request,
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Saved → draft: editable again; the stored layout is dropped (R12)."""
    return await _move(db, application_id, "revert", current_user, request)


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


@router.get(
    "/{application_id}/blank-form", response_class=Response, responses=PDF_RESPONSES
)
async def blank_form(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> Response:
    """A saved or published template's form with no member in it and no
    watermark, as ``application-<id>-blank.pdf``, for filling in by hand
    (R27): a published one's stored drawing, a saved one's drawn now; a
    draft → 422 ``APPLICATION_NOT_SAVED``."""
    try:
        content = await ApplicationDownloadService(db).blank_form(application_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return pdf_response(content, application_id, BLANK_FORM)


@router.get("/{application_id}/form", response_class=Response, responses=PDF_RESPONSES)
async def own_form(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    category: CategoryChoice = None,
) -> Response:
    """The signed-in member's own form of a published application (R10, R11, R11a)."""
    try:
        content = await ApplicationDownloadService(db).own_form(
            current_user, application_id, category
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
    category: CategoryChoice = None,
) -> Response:
    """An admin's download of any member's form of a published application (R20)."""
    try:
        content = await ApplicationDownloadService(db).member_form(
            application_id, username, category
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return pdf_response(content, application_id, username)
