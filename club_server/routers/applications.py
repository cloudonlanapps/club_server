"""Application templates: list, read, create, edit, delete (#93).

Admins see and write every template; a member who has uploaded an identity
document sees the published ones. The label catalogue is served for the
editor; the clauses and the club's library are in ``application_library.py``,
the state moves, duplicate, preview and the forms in ``application_forms.py``.
While the module is off every route answers 503 ``APPLICATIONS_DISABLED``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_authenticated_user,
    get_db,
    require_admin,
    require_applications_enabled,
)
from ..schemas.application import (
    ApplicationCreate,
    ApplicationLabelResponse,
    ApplicationLabelsResponse,
    ApplicationLabelUpdate,
    ApplicationResponse,
    ApplicationStatus,
    ApplicationUpdate,
)
from ..schemas.common import PaginatedResponse
from ..services.application import ApplicationService, application_response
from ..services.application_languages import club_languages, shown
from ..services.application_labels import ApplicationLabelService, club_labels
from ..services.application_downloads import ApplicationDownloadService
from . import application_errors as err

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(require_applications_enabled)],
)


@router.get("/labels", response_model=ApplicationLabelsResponse)
async def get_labels(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationLabelsResponse:
    """The label catalogue in the club's languages, to pre-fill the editor
    (R8, R30)."""
    club = await club_languages(db)
    stored = await club_labels(db)
    return ApplicationLabelsResponse(
        languages=club, labels={k: shown(v, club) for k, v in stored.items()}
    )


@router.patch("/labels/{key}", response_model=ApplicationLabelResponse)
async def update_label(
    key: str,
    payload: ApplicationLabelUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationLabelResponse:
    """Change a label's text in the club's languages (R8a)."""
    try:
        row = await ApplicationLabelService(db).update(
            key, payload.text, current_user.username
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    club = await club_languages(db)
    return ApplicationLabelResponse(key=row.key, text=shown(row.text, club))


@router.get("", response_model=PaginatedResponse[ApplicationResponse])
async def list_applications(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
    status_filter: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[ApplicationResponse]:
    """Admins: every template, optionally of one status; members: the
    published ones, once they have an identity document (R10)."""
    return await ApplicationDownloadService(db).list_for(
        current_user, status_filter, offset, limit
    )


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> ApplicationResponse:
    """One template, as the caller may see it (R10); a member's read lists
    the age categories they fit (R11a)."""
    service = ApplicationDownloadService(db)
    try:
        application = await service.get_for(current_user, application_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(
        application,
        await club_languages(db),
        service.fitting_for(current_user, application),
    )


@router.post(
    "", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED
)
async def create_application(
    payload: ApplicationCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Create a draft (R2)."""
    try:
        application = await ApplicationService(db).create(
            payload, current_user.username
        )
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(application, await club_languages(db))


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: int,
    payload: ApplicationUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Change a draft: the fields sent replace the stored ones (R3)."""
    try:
        application = await ApplicationService(db).update(application_id, payload)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(application, await club_languages(db))


@router.delete("/{application_id}", response_model=ApplicationResponse)
async def delete_application(
    application_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationResponse:
    """Soft delete a draft or saved template (R4)."""
    try:
        application = await ApplicationService(db).soft_delete(application_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return application_response(application, await club_languages(db))
