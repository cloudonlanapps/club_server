"""Unauthenticated club-info read for the public website (#296)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..schemas.club_info import PublicClubInfoResponse
from ..services.club_info import ClubInfoService

router = APIRouter(prefix="/public/club-info", tags=["public"])


@router.get("", response_model=PublicClubInfoResponse)
async def get_public_club_info(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PublicClubInfoResponse:
    """The club_info object and the site_media map; {} where unset (public R16)."""
    return await ClubInfoService(db).public_document()
