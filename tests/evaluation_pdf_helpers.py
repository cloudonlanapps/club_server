"""Reading the member copy PDF in tests (#535).

The PDF embeds its fonts, so its text is written as glyph ids rather than
readable bytes; tests read it back through ``pypdf``.
"""

import io
import re

from httpx import AsyncClient, Response
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import User

from .evaluation_helpers import auth

# "p / n" at the foot of every logical page.
PAGE_NUMBER = re.compile(r"(\d+) / (\d+)")


def pdf_reader(content: bytes) -> PdfReader:
    """The PDF, parsed."""
    return PdfReader(io.BytesIO(content))


def sheet_texts(content: bytes) -> list[str]:
    """The text of each printed sheet, whitespace collapsed to single spaces."""
    return [
        " ".join((page.extract_text() or "").split())
        for page in pdf_reader(content).pages
    ]


def pdf_text(content: bytes) -> str:
    """All the PDF's text, whitespace collapsed to single spaces."""
    return " ".join(sheet_texts(content))


async def preview(client: AsyncClient, token: str, evaluation_id: int) -> Response:
    """The owner's unstored preview of the member copy."""
    return await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}/pdf", headers=auth(token)
    )


async def rename(db_session: AsyncSession, username: str, first: str, last: str):
    """Give a user a full name."""
    user = await db_session.get(User, username)
    assert user is not None
    user.first_name = first
    user.last_name = last
    await db_session.flush()
