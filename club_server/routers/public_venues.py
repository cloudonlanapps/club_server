"""Unauthenticated venue reads for the public website (#307).

The website never reads ``/v1/venues``; those are member endpoints. This is
the whole public venue surface: a plain list and one venue by public id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..schemas.venue import PublicVenueResponse
from ..services.public_venue import PublicVenueService

router = APIRouter(prefix="/public/venues", tags=["public"])


@router.get("", response_model=list[PublicVenueResponse])
async def list_public_venues(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> list[PublicVenueResponse]:
    """List live venues for anonymous readers (venue R22)."""
    return await PublicVenueService(db).list_public_venues()


@router.get("/{public_id}", response_model=PublicVenueResponse)
async def get_public_venue(
    public_id: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PublicVenueResponse:
    """One live venue by public id; unknown or deleted → 404 (venue R23, R24)."""
    return await PublicVenueService(db).get_public_venue(public_id)
