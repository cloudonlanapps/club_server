"""The club's library of texts, and the clauses list (#126, #133, applications R7–R7b).

Club texts are stored once and offered in every template; the built-in
clauses are seeded into them on first use (``application_seed``), so the
clauses list is the library. Admins create, edit, read and delete every
text. Deleting one changes no template: a pasted text is the template's own
(#107).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.application_library_text import ApplicationLibraryText
from ..exceptions import ApplicationLibraryTextNotFoundException
from ..schemas.application_library import (
    ApplicationClausesResponse,
    ApplicationLibraryTextCreate,
    ApplicationLibraryTextResponse,
    ApplicationLibraryTextUpdate,
)
from ..utils import now_utc_ms
from .application_catalogue import library
from .application_languages import check_written, club_languages, kept, shown
from .application_seed import ensure_library_seeded

CONTENT_FIELDS = tuple(ApplicationLibraryTextCreate.model_fields)


def library_text_response(
    row: ApplicationLibraryText, club: list[str]
) -> ApplicationLibraryTextResponse:
    """A club text as returned, in the club's languages ``club`` (R30)."""
    return ApplicationLibraryTextResponse.model_validate(
        {
            "id": row.id,
            "key": row.key,
            "kind": row.kind,
            "name": row.name,
            "heading": shown(row.heading, club),
            "text": shown(row.text, club),
            "created_by": row.created_by,
            "created_at_utc": row.created_at,
            "updated_at_utc": row.updated_at,
        }
    )


def _content(value: ApplicationLibraryTextCreate) -> dict[str, Any]:
    dumped: dict[str, Any] = value.model_dump(mode="json")
    return {field: dumped[field] for field in CONTENT_FIELDS}


class ApplicationLibraryService:
    """The club's texts."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def list_all(self) -> list[ApplicationLibraryText]:
        """Every club text, oldest first."""
        await ensure_library_seeded(self.db)
        rows = await self.db.execute(
            select(ApplicationLibraryText).order_by(ApplicationLibraryText.id)
        )
        return list(rows.scalars().all())

    async def get_or_raise(self, text_id: int) -> ApplicationLibraryText:
        """One club text, or ``ApplicationLibraryTextNotFoundException``."""
        await ensure_library_seeded(self.db)
        row = await self.db.get(ApplicationLibraryText, text_id)
        if row is None:
            raise ApplicationLibraryTextNotFoundException(text_id)
        return row

    async def create(
        self, content: ApplicationLibraryTextCreate, created_by: str
    ) -> ApplicationLibraryText:
        """A new club text, in the club's languages (R30)."""
        await ensure_library_seeded(self.db)
        club = await club_languages(self.db)
        check_written([content.heading, content.text], club)
        now = now_utc_ms()
        row = ApplicationLibraryText(
            created_by=created_by, created_at=now, updated_at=now, **_content(content)
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def update(
        self, text_id: int, changes: ApplicationLibraryTextUpdate
    ) -> ApplicationLibraryText:
        """Replace the fields sent; the result is checked as a new text is,
        so a pydantic ``ValidationError`` leaves the text unchanged. A text
        or heading sent keeps its translations in languages the club has
        removed (R30)."""
        row = await self.get_or_raise(text_id)
        club = await club_languages(self.db)
        merged = {field: getattr(row, field) for field in CONTENT_FIELDS}
        for field in changes.model_fields_set:
            value = getattr(changes, field)
            if field in ("heading", "text") and value is not None:
                check_written(value, club)
                value = kept(value, getattr(row, field), club)
            merged[field] = value
        checked = ApplicationLibraryTextCreate.model_validate(merged)
        for field, value in _content(checked).items():
            setattr(row, field, value)
        row.updated_at = now_utc_ms()
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def delete(self, text_id: int) -> None:
        """Remove a club text."""
        row = await self.get_or_raise(text_id)
        await self.db.delete(row)
        await self.db.flush()

    async def clauses(self) -> ApplicationClausesResponse:
        """The library's texts, oldest first, in the club's languages, with
        those languages and the choices a fact is printed with (R7, R7b, R30)."""
        club = await club_languages(self.db)
        return ApplicationClausesResponse(
            languages=club,
            choices=library()["choices"],
            clauses=[library_text_response(row, club) for row in await self.list_all()],
        )
