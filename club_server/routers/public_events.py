"""Unauthenticated event catalogue for the public website (#299)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..schemas.common import PaginatedResponse
from ..schemas.public_event import PublicEventResponse
from ..services.public_event import PublicEventService

router = APIRouter(prefix="/public/events", tags=["public"])


@router.get("", response_model=PaginatedResponse[PublicEventResponse])
async def list_public_events(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    event_type: Annotated[
        Literal["programme", "camp", "oneOff"] | None, Query(alias="type")
    ] = None,
    from_time_utc: Annotated[int | None, Query(alias="from")] = None,
    to_time_utc: Annotated[int | None, Query(alias="to")] = None,
    featured: Annotated[bool | None, Query()] = None,
    venue_id: Annotated[str | None, Query(alias="venueId")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[PublicEventResponse]:
    """The catalogue: public, live events by type, venue, featured flag and window."""
    return await PublicEventService(db).list_public_events(
        event_type=event_type,
        from_time_utc=from_time_utc,
        to_time_utc=to_time_utc,
        featured=featured,
        venue_public_id=venue_id,
        offset=offset,
        limit=limit,
    )


@router.get("/{public_id}", response_model=PublicEventResponse)
async def get_public_event(
    public_id: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PublicEventResponse:
    """One public, live event by public id; anything else → 404."""
    return await PublicEventService(db).get_public_event(public_id)
