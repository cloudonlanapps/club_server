"""Event Marketing module: public reads (#410, marketing R10–R11).

Mounted before the catalogue's ``/{public_id}`` route so ``/marketing``
is not taken for a public id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db, require_event_marketing_enabled
from ..schemas.event_marketing_extended import PublicEventMarketingResponse
from ..services.event_marketing import PUBLIC_BATCH_MAX, EventMarketingService

router = APIRouter(
    prefix="/public/events",
    tags=["public"],
    dependencies=[Depends(require_event_marketing_enabled)],
)


@router.get("/marketing", response_model=list[PublicEventMarketingResponse])
async def list_public_event_marketing(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    ids: Annotated[str, Query(min_length=1)],
) -> list[PublicEventMarketingResponse]:
    """Blocks for up to 50 comma-separated public ids, for listing cards."""
    public_ids = [p for p in ids.split(",") if p]
    if len(public_ids) > PUBLIC_BATCH_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TOO_MANY_IDS",
                "message": f"At most {PUBLIC_BATCH_MAX} ids per request",
            },
        )
    return await EventMarketingService(db).list_public(public_ids)


@router.get("/{public_id}/marketing", response_model=PublicEventMarketingResponse)
async def get_public_event_marketing(
    public_id: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PublicEventMarketingResponse:
    """The block of one public, live event; anything else → 404."""
    return await EventMarketingService(db).get_public(public_id)
