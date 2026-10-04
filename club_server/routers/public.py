"""Unauthenticated, read-only public profile endpoints.

Exposes privacy-safe coach profiles keyed by an HMAC ``public_id`` (never
the username) so anonymous website visitors and logged-in members share one
projection. Who appears is consent (``is_public_profile``, the coach's own)
joined with admin curation (#332): the listing row can withhold or order a
coach, never grant visibility.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..schemas.user import PublicProfileResponse
from ..services.staff_listing import StaffListingService
from ..utils import generate_public_id

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/staff", response_model=list[PublicProfileResponse])
async def list_public_staff(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    include_guests: Annotated[bool, Query()] = False,
) -> list[PublicProfileResponse]:
    """The staff page (public R1–R3).

    Consenting coaches in curated order; hidden coaches withheld; guests
    withheld unless ``include_guests``.
    """
    return await StaffListingService(db).public_staff(include_guests=include_guests)


@router.get("/profile/by_id/{public_id}", response_model=PublicProfileResponse)
async def get_public_profile(
    public_id: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PublicProfileResponse:
    """Get a single public profile by its ``public_id``.

    Resolves only coaches who have consented (guests included, whatever
    their curation). Anything else (unknown id, non-coach, opted-out) → 404.
    """
    service = StaffListingService(db)
    for user in await service._consenting_coaches():
        if generate_public_id(user.username) == public_id:
            return await service.public_profile(user)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "USER_NOT_FOUND", "message": "Profile not found"},
    )
