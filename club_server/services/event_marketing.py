"""Event Marketing module service (#410, marketing R6–R13a).

The block carries its own version (#13, R13) on an occurrence's terms: an
event with no row is at version 1, the first replace writes the row at 2,
and every replace after bumps it. The event's version is not involved.
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.event import Event
from ..db.models.event_marketing import DEFAULT_CURRENCY, EventMarketing
from ..exceptions import (
    EventMarketingNotFoundException,
    StaleMarketingVersionException,
)
from ..schemas.event_marketing_extended import (
    EventMarketingResponse,
    EventMarketingWrite,
    PublicEventMarketingResponse,
)
from ..utils import generate_event_public_id, now_utc_ms

_JSON_FIELDS = (
    "fee_structure",
    "package_offers",
    "offers",
    "club_membership",
    "facilities",
)
PUBLIC_BATCH_MAX = 50
# An event nobody has written a block for has no row and is at version 1;
# the first replace writes the row at 2 (R13).
UNWRITTEN_MARKETING_VERSION = 1


def check_marketing_version(row: EventMarketing | None, expected_version: int) -> None:
    """R13a: refuse a write carrying a version the block has moved past."""
    if row is None:
        current, updated_at, updated_by = UNWRITTEN_MARKETING_VERSION, None, None
    else:
        current, updated_at, updated_by = row.version, row.updated_at, row.updated_by
    if current != expected_version:
        raise StaleMarketingVersionException(current, updated_at, updated_by)


class EventMarketingService:
    """Reads and whole-row writes of the extended block."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def _row(self, event_id: int) -> EventMarketing | None:
        return (
            await self.db.execute(
                select(EventMarketing).where(EventMarketing.event_id == event_id)
            )
        ).scalar_one_or_none()

    async def get(self, event_id: int) -> EventMarketingResponse:
        """The block, or 404 when the event has none (R6)."""
        row = await self._row(event_id)
        if row is None:
            raise EventMarketingNotFoundException(event_id)
        return EventMarketingResponse.from_model(row)

    async def replace(
        self, event_id: int, data: EventMarketingWrite, actor: str | None = None
    ) -> EventMarketingResponse:
        """Whole-row write: fields not sent are cleared (R6, R9).

        Refused unless ``data.version`` is the block's current one (R13a);
        the write then moves the block one version on (R13).
        """
        now = now_utc_ms()
        row = await self._row(event_id)
        check_marketing_version(row, data.version)
        if row is None:
            row = EventMarketing(
                event_id=event_id,
                currency=DEFAULT_CURRENCY,
                created_at=now,
                version=UNWRITTEN_MARKETING_VERSION,
            )
            self.db.add(row)
        row.version += 1
        row.updated_by = actor
        values = data.model_dump(exclude={"version"})
        for name, value in values.items():
            if name in _JSON_FIELDS:
                value = json.dumps(value) if value is not None else None
            setattr(row, name, value)
        row.updated_at = now
        await self.db.flush()
        return EventMarketingResponse.from_model(row)

    async def delete(self, event_id: int, expected_version: int) -> None:
        """Remove the block at its current version (R13a); absent is not an
        error at version 1 (R6)."""
        row = await self._row(event_id)
        check_marketing_version(row, expected_version)
        if row is not None:
            await self.db.delete(row)
            await self.db.flush()

    # --- public ---------------------------------------------------------

    async def _public_rows(self) -> list[tuple[EventMarketing, str]]:
        result = await self.db.execute(
            select(EventMarketing)
            .join(Event, Event.id == EventMarketing.event_id)
            .where(Event.visibility == "public", Event.deleted_at.is_(None))
        )
        return [
            (row, generate_event_public_id(row.event_id))
            for row in result.scalars().all()
        ]

    async def get_public(self, public_id: str) -> PublicEventMarketingResponse:
        """The block of a public, live event by public id, else 404 (R10, R12)."""
        for row, pid in await self._public_rows():
            if pid == public_id:
                return PublicEventMarketingResponse.from_model(row)
        raise EventMarketingNotFoundException(public_id)

    async def list_public(
        self, public_ids: list[str]
    ) -> list[PublicEventMarketingResponse]:
        """Blocks for the given public ids; unknown or private ones are absent (R11)."""
        wanted = set(public_ids)
        return [
            PublicEventMarketingResponse.from_model(row)
            for row, pid in await self._public_rows()
            if pid in wanted
        ]
