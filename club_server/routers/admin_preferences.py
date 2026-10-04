"""Admin endpoints for the system_preferences key/value store (#57)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_super_admin
from ..schemas.system_preference import (
    SystemPreferenceList,
    SystemPreferenceResponse,
    SystemPreferenceUpdate,
)
from ..services.club_info import validate_preference_value
from ..services.system_preferences import SystemPreferenceService
from ..utils import get_client_ip


router = APIRouter(prefix="/admin/preferences", tags=["Admin"])


@router.get("", response_model=SystemPreferenceList)
async def list_preferences(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Every preference in force: the stored ones and each defaulted key
    never written."""
    _ = current_user
    service = SystemPreferenceService(db)
    return SystemPreferenceList(items=await service.read_all())


@router.get("/{key}", response_model=SystemPreferenceResponse)
async def get_preference(
    key: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """A preference, or its default (else null) when never written."""
    _ = current_user
    return await SystemPreferenceService(db).read(key)


@router.patch("/{key}", response_model=SystemPreferenceResponse)
async def update_preference(
    request: Request,
    key: str,
    payload: SystemPreferenceUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Write a preference; the write is audited (platform R7)."""
    await validate_preference_value(db, key, payload.value)
    service = SystemPreferenceService(db)
    row = await service.write(
        key, payload.value, current_user.username, get_client_ip(request)
    )
    return SystemPreferenceService.to_response(row)
