"""Evidence attached to an evaluation (#302, #535, R55, R56, R56a).

Built on the public ``MediaLinkService`` rather than the private handlers
in ``media_links.py``, so the evaluation module stays detachable and that
file does not grow further. Only the effective owner reads or writes it;
writes are draft content, so they need a draft and are not audited (R52).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation
from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_evaluations_enabled,
)
from ..exceptions import (
    MediaLinkExistsException,
    MediaLinkNotFoundException,
    MediaLinkTagFullException,
    MediaLinkTooManyTagsException,
    MediaNotFoundException,
)
from ..schemas.media_links import MediaLinkCreate, MediaLinkPatch
from ..services.evaluation import EvaluationService
from ..services.evaluation_media import OWNER_TYPE, check_evidence
from ..services.media_links import MediaLinkService
from . import evaluation_errors as err
from .evaluations import load_owned

router = APIRouter(
    prefix="/evaluations/by_id/{evaluation_id}/media",
    tags=["evaluation-media"],
    dependencies=[Depends(require_evaluations_enabled)],
)


async def load_evaluation(
    db: AsyncSession, evaluation_id: int, current_user: User, *, write: bool = False
) -> Evaluation:
    """The caller's own evaluation (R35); a draft, for a write (R56a)."""
    evaluation = await load_owned(EvaluationService(db), evaluation_id, current_user)
    if write:
        try:
            EvaluationService.require_draft(evaluation)
        except err.DOMAIN_ERRORS as exc:
            raise err.to_http(exc)
    return evaluation


def map_link_errors(exc: Exception) -> HTTPException:
    """Translate link-service failures into the codes the media module uses."""
    if isinstance(exc, MediaNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MEDIA_NOT_FOUND", "message": "Media not found"},
        )
    if isinstance(exc, MediaLinkExistsException):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MEDIA_LINK_EXISTS",
                "message": "Link with this (tag, mediaUuid) already exists",
            },
        )
    if isinstance(exc, MediaLinkTagFullException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MEDIA_LINK_TAG_FULL",
                "message": f"Tag {exc.tag} already has the maximum {exc.limit} links",
            },
        )
    if isinstance(exc, MediaLinkTooManyTagsException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MEDIA_LINK_TOO_MANY_TAGS",
                "message": f"Already at the maximum {exc.limit} distinct media tags",
            },
        )
    if isinstance(exc, MediaLinkNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MEDIA_LINK_NOT_FOUND", "message": "Link not found"},
        )
    raise exc


@router.get("")
async def list_grouped(
    evaluation_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """All evidence on this evaluation, grouped by question id."""
    _ = await load_evaluation(db, evaluation_id, current_user)
    return await MediaLinkService(db, OWNER_TYPE).list_grouped(
        evaluation_id, viewer=current_user
    )


@router.get("/{tag}")
async def list_by_tag(
    evaluation_id: int,
    tag: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """The evidence for one question."""
    _ = await load_evaluation(db, evaluation_id, current_user)
    return await MediaLinkService(db, OWNER_TYPE).list_by_tag(
        evaluation_id, tag, viewer=current_user
    )


@router.get("/{tag}/{media_uuid}")
async def get_one(
    evaluation_id: int,
    tag: str,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """One link, with its media metadata inline."""
    _ = await load_evaluation(db, evaluation_id, current_user)
    try:
        return await MediaLinkService(db, OWNER_TYPE).get(
            evaluation_id, tag, media_uuid, viewer=current_user
        )
    except Exception as exc:
        raise map_link_errors(exc)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_link(
    evaluation_id: int,
    body: MediaLinkCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Attach evidence to one question of a draft (R56a)."""
    evaluation = await load_evaluation(db, evaluation_id, current_user, write=True)
    try:
        await check_evidence(db, evaluation, body.tag, body.media_uuid)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    try:
        return await MediaLinkService(db, OWNER_TYPE).create(
            evaluation_id,
            body.tag,
            body.media_uuid,
            body.metadata,
            viewer=current_user,
        )
    except Exception as exc:
        raise map_link_errors(exc)


@router.patch("/{tag}/{media_uuid}")
async def patch_link(
    evaluation_id: int,
    tag: str,
    media_uuid: str,
    body: MediaLinkPatch,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Update a link's metadata. Tag and mediaUuid are its identity."""
    _ = await load_evaluation(db, evaluation_id, current_user, write=True)
    try:
        return await MediaLinkService(db, OWNER_TYPE).update_metadata(
            evaluation_id, tag, media_uuid, body.metadata
        )
    except Exception as exc:
        raise map_link_errors(exc)


@router.delete("/{tag}/{media_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_one(
    evaluation_id: int,
    tag: str,
    media_uuid: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> None:
    """Detach one file. The media itself is untouched."""
    _ = await load_evaluation(db, evaluation_id, current_user, write=True)
    try:
        _ = await MediaLinkService(db, OWNER_TYPE).delete_one(
            evaluation_id, tag, media_uuid
        )
    except Exception as exc:
        raise map_link_errors(exc)


@router.delete("/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    evaluation_id: int,
    tag: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> None:
    """Detach every file under one question."""
    _ = await load_evaluation(db, evaluation_id, current_user, write=True)
    try:
        _ = await MediaLinkService(db, OWNER_TYPE).delete_tag(evaluation_id, tag)
    except Exception as exc:
        raise map_link_errors(exc)
