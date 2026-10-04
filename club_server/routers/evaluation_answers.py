"""Answers, written and cleared one at a time on a draft, and evidence uploaded
for one of them (#535, R9, R10, R21, R56d).

Not audited: editing a draft is its writer's working copy (R52).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_evaluations_enabled,
)
from ..exceptions import FileTooLargeException
from ..schemas.evaluation import EvaluationAnswerInput, EvaluationStaffView
from ..services.evaluation import EvaluationService
from ..services.evaluation_answers import EvaluationAnswerService
from ..services.evaluation_media import upload_evidence
from ..services.evaluation_views import staff_view
from . import evaluation_errors as err
from .evaluations import load_owned

router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(require_evaluations_enabled)],
)


@router.put(
    "/by_id/{evaluation_id}/answers/{item_id}", response_model=EvaluationStaffView
)
async def put_answer(
    evaluation_id: int,
    item_id: int,
    payload: EvaluationAnswerInput,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Write one answer, replacing any earlier one; validated against its item (R10)."""
    evaluation = await load_owned(EvaluationService(db), evaluation_id, current_user)
    try:
        EvaluationService.require_draft(evaluation)
        await EvaluationAnswerService(db).put_answer(evaluation, item_id, payload)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return await staff_view(db, evaluation)


@router.delete(
    "/by_id/{evaluation_id}/answers/{item_id}", response_model=EvaluationStaffView
)
async def clear_answer(
    evaluation_id: int,
    item_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Clear one answer and detach its evidence (R21, R22a)."""
    evaluation = await load_owned(EvaluationService(db), evaluation_id, current_user)
    try:
        EvaluationService.require_draft(evaluation)
        await EvaluationAnswerService(db).clear_answer(evaluation, item_id)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    return await staff_view(db, evaluation)


@router.post(
    "/by_id/{evaluation_id}/evidence/{item_id}",
    response_model=EvaluationStaffView,
    status_code=status.HTTP_201_CREATED,
)
async def upload_answer_evidence(
    evaluation_id: int,
    item_id: int,
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> EvaluationStaffView:
    """Upload a file as evidence for one question of a draft (R56d)."""
    evaluation = await load_owned(EvaluationService(db), evaluation_id, current_user)
    try:
        EvaluationService.require_draft(evaluation)
        _ = await upload_evidence(db, evaluation, item_id, file)
    except err.DOMAIN_ERRORS as exc:
        raise err.to_http(exc)
    except FileTooLargeException as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "FILE_TOO_LARGE", "message": str(exc)},
        )
    return await staff_view(db, evaluation)
