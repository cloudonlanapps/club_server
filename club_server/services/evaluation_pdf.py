"""The member copy of a published evaluation, as a PDF (#535, R63, R64).

Built from the member projection, so it holds no private item. Its layout
is the ``cl_survey_forms`` prototype's: tall half-width pages (105 × 297 mm)
two to an A4 sheet, each framed; the template's name and the club logo, the
member and the review period; a Question | Rating table with drawn ratings
and handwritten answers; the written answers closing the layout; and the
signature of the effective owner. Printed text is Lato, filled-in text
Patrick Hand.

Blocks are measured, paginated (``evaluation_pdf_paginate``) and then drawn.
The look is one per deployment, not per template.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.event import Event
from ..db.models.user import User
from ..mailer.config import email_settings
from ..schemas.evaluation import EvaluationMemberView
from .evaluation_pdf_blocks import signature_block
from .evaluation_pdf_canvas import EvaluationPdfCanvas
from .evaluation_pdf_header import header_blocks
from .evaluation_pdf_layout import CONTENT_HEIGHT
from .evaluation_pdf_logo import club_logo
from .evaluation_pdf_page import draw_sheets
from .evaluation_pdf_paginate import paginate
from .evaluation_pdf_table import body_blocks
from .evaluation_pdf_text import date_text


async def _full_name(db: AsyncSession, username: str) -> str:
    user = await db.get(User, username)
    if user is None:
        return username
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name or username


async def _event_title(db: AsyncSession, event_id: int | None) -> str | None:
    if event_id is None:
        return None
    event = await db.get(Event, event_id)
    return event.title if event else None


async def member_copy_pdf(db: AsyncSession, view: EvaluationMemberView) -> bytes:
    """The PDF of a member view."""
    base_url = f"{email_settings.server_base_url.rstrip('/')}{settings.api_v1_prefix}"
    blocks = [
        *header_blocks(
            view.template.name,
            await _full_name(db, view.created_for),
            date_text(view.period_start_utc),
            date_text(view.period_end_utc),
            await club_logo(db),
            event=await _event_title(db, view.event_id),
            review_date=(
                date_text(view.published_at_utc)
                if view.published_at_utc is not None
                else None
            ),
        ),
        *body_blocks(view, base_url),
        signature_block(await _full_name(db, view.owner or view.created_by)),
    ]
    canvas = EvaluationPdfCanvas()
    canvas.set_title(view.template.name)
    pages = paginate(
        [canvas.measure(block.draw) for block in blocks],
        [block.keep_with_next for block in blocks],
        [block.section for block in blocks],
        CONTENT_HEIGHT,
    )
    draw_sheets(canvas, blocks, pages)
    return bytes(canvas.output())
