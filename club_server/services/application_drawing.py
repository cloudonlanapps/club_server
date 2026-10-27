"""A published template's stored drawing: made, kept and dropped (#128, R25, R26).

Publishing draws the template's form once with no member in it and stores
it with the places of the member's values (``draw_blank``). A download
uses the stored drawing while it was drawn for the template's current
publish ID, the club's current logo and this server's way of placing
values (``PLACES_VERSION``); otherwise it is drawn again first and stored,
so a logo change, a republish or a template published before drawings were
stored is drawn once more, on its next download. Drawing runs in a worker
thread, so the API answers other requests meanwhile.
"""

import asyncio
import hashlib

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..club_calendar import club_today, day_to_date
from ..db.models.application import Application
from ..db.models.application_drawing import ApplicationDrawing
from ..utils import now_utc_ms
from .application_catalogue import JsonDict
from .application_document import template_document
from .application_form import draw_blank, frozen_options
from .application_pdf_builder import PLACES_VERSION


def logo_key(logo: bytes | None) -> str:
    """What a drawing records of the logo it was drawn with."""
    return hashlib.sha256(logo).hexdigest() if logo else ""


class ApplicationDrawingService:
    """The stored drawing of each published template."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def current(
        self, application: Application, logo: bytes | None, options: JsonDict
    ) -> ApplicationDrawing:
        """The drawing a download fills: the stored one, or a new one when
        it was drawn for another publish, logo or version."""
        found = (
            await self.db.execute(
                select(ApplicationDrawing).where(
                    ApplicationDrawing.application_id == application.id
                )
            )
        ).scalar_one_or_none()
        if (
            found is not None
            and found.publish_id == application.publish_id
            and found.logo_key == logo_key(logo)
            and found.places.get("version") == PLACES_VERSION
        ):
            return found
        return await self.store(application, logo, options)

    async def store(
        self, application: Application, logo: bytes | None, options: JsonDict
    ) -> ApplicationDrawing:
        """Draw the published ``application``'s form with no member in it
        and store it, replacing any drawing it had. ``options`` are the
        live profile options; the stored layout's frozen ones win (R14)."""
        publish_id = application.publish_id
        if publish_id is None:
            raise ValueError(f"application {application.id} is not published")
        pdf, places = await self.draw(application, logo, options, publish_id)
        values = {
            "publish_id": publish_id,
            "logo_key": logo_key(logo),
            "pdf": pdf,
            "places": places,
            "drawn_at": now_utc_ms(),
        }
        _ = await self.db.execute(
            insert(ApplicationDrawing)
            .values(application_id=application.id, **values)
            .on_conflict_do_update(
                index_elements=[ApplicationDrawing.application_id], set_=values
            )
        )
        drawing = await self.db.get(
            ApplicationDrawing, application.id, populate_existing=True
        )
        assert drawing is not None
        return drawing

    async def draw(
        self,
        application: Application,
        logo: bytes | None,
        options: JsonDict,
        publish_id: str | None,
    ) -> tuple[bytes, JsonDict]:
        """The saved or published ``application``'s form with no member in
        it, and its places, drawn in a worker thread from its stored layout;
        ``publish_id`` is printed in the side margin when given. Not stored:
        a saved template's blank form (R27) is drawn per request."""
        stored = application.layout
        if stored is None:
            raise ValueError(f"application {application.id} has no stored layout")
        return await asyncio.to_thread(
            draw_blank,
            template_document(application),
            frozen_options(stored, options),
            logo,
            day_to_date(club_today()),
            stored,
            publish_id,
        )

    async def drop(self, application_id: int) -> None:
        """Forget a template's drawing (it is unpublished)."""
        _ = await self.db.execute(
            delete(ApplicationDrawing).where(
                ApplicationDrawing.application_id == application_id
            )
        )
