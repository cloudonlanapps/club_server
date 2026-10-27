"""The clauses list and the club's library of texts (#126, #133, applications R7–R7b).

``GET /applications/clauses`` lists the club's library texts, the built-in
clauses seeded into it among them, for the editor to paste as written. Admins create, read, edit and delete the club's texts under
``/applications/library``. While the module is off every route answers 503
``APPLICATIONS_DISABLED``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_admin, require_applications_enabled
from ..schemas.application_library import (
    ApplicationClausesResponse,
    ApplicationLibraryTextCreate,
    ApplicationLibraryTextResponse,
    ApplicationLibraryTextUpdate,
)
from ..services.application_library import (
    ApplicationLibraryService,
    library_text_response,
)
from . import application_errors as err

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(require_applications_enabled)],
)

Db = Annotated[AsyncSession, Depends(get_db, scope="function")]
Admin = Annotated[User, Depends(require_admin())]


@router.get("/clauses", response_model=ApplicationClausesResponse)
async def get_clauses(db: Db, _current_user: Admin) -> ApplicationClausesResponse:
    """The club's library texts, for the editor (R7, R7b)."""
    return await ApplicationLibraryService(db).clauses()


@router.get("/library", response_model=list[ApplicationLibraryTextResponse])
async def list_library_texts(
    db: Db, _current_user: Admin
) -> list[ApplicationLibraryTextResponse]:
    """Every club text, oldest first (R7a)."""
    rows = await ApplicationLibraryService(db).list_all()
    return [library_text_response(row) for row in rows]


@router.post(
    "/library",
    response_model=ApplicationLibraryTextResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_library_text(
    payload: ApplicationLibraryTextCreate, db: Db, current_user: Admin
) -> ApplicationLibraryTextResponse:
    """Add a club text (R7a)."""
    row = await ApplicationLibraryService(db).create(payload, current_user.username)
    return library_text_response(row)


@router.get("/library/{text_id}", response_model=ApplicationLibraryTextResponse)
async def get_library_text(
    text_id: int, db: Db, _current_user: Admin
) -> ApplicationLibraryTextResponse:
    """One club text (R7a)."""
    try:
        row = await ApplicationLibraryService(db).get_or_raise(text_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return library_text_response(row)


@router.patch("/library/{text_id}", response_model=ApplicationLibraryTextResponse)
async def update_library_text(
    text_id: int, payload: ApplicationLibraryTextUpdate, db: Db, _current_user: Admin
) -> ApplicationLibraryTextResponse:
    """Change a club text: the fields sent replace the stored ones (R7a)."""
    try:
        row = await ApplicationLibraryService(db).update(text_id, payload)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())
    return library_text_response(row)


@router.delete("/library/{text_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_library_text(text_id: int, db: Db, _current_user: Admin) -> Response:
    """Remove a club text; no template changes (R7a)."""
    try:
        await ApplicationLibraryService(db).delete(text_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
