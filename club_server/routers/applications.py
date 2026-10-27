"""Application templates: list, read, create, edit, delete (#93).

Admins see and write every template; a member who has uploaded an identity
document sees the published ones. The label catalogue and the clause
library are served for the editor, which fills one clause at a time and
inserts the text it gets back (#107). The state moves, duplicate, preview and
the forms are in ``application_forms.py``. While the module is off every
route answers 503 ``APPLICATIONS_DISABLED``.
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
    ApplicationClauseFill,
    ApplicationClauseFilled,
    ApplicationClausesResponse,
    ApplicationCreate,
    ApplicationLabelsResponse,
    ApplicationResponse,
    ApplicationStatus,
    ApplicationUpdate,
)
from ..schemas.common import PaginatedResponse
from ..services.application import ApplicationService, application_response
from ..services.application_catalogue import catalogue_languages, labels, library
from ..services.application_document import fact_document
from ..services.application_downloads import ApplicationDownloadService
from ..services.application_text import fill_clause
from . import application_errors as err

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(require_applications_enabled)],
)


@router.get("/labels", response_model=ApplicationLabelsResponse)
async def get_labels(
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationLabelsResponse:
    """The label catalogue, to pre-fill the editor (R8)."""
    return ApplicationLabelsResponse(languages=catalogue_languages(), labels=labels())


@router.get("/clauses", response_model=ApplicationClausesResponse)
async def get_clauses(
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationClausesResponse:
    """The clause library, for the editor's pickers (R7)."""
    lib = library()
    return ApplicationClausesResponse.model_validate(
        {
            "languages": list(lib["formats"]["months"]),
            "choices": lib["choices"],
            "clauses": lib["clauses"],
        }
    )


@router.post("/clauses/fill", response_model=ApplicationClauseFilled)
async def fill_one_clause(
    payload: ApplicationClauseFill,
    _current_user: Annotated[User, Depends(require_admin())],
) -> ApplicationClauseFilled:
    """One library clause filled from the facts given for its gaps, in every
    library language, for the editor to insert as custom text (R9a). No
    template is read or written."""
    facts = {
        gap: fact_document(fact.model_dump(mode="json", by_alias=True))
        for gap, fact in payload.facts.items()
    }
    try:
        filled = fill_clause(payload.clause, facts, library())
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return ApplicationClauseFilled.model_validate({"clause": payload.clause, **filled})


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
        application, service.fitting_for(current_user, application)
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
    application = await ApplicationService(db).create(payload, current_user.username)
    return application_response(application)


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
    return application_response(application)


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
    return application_response(application)
