"""The club's labels, the form's fixed wording (#133, applications R8, R8a).

Seeded on first use from the label catalogue (``application_seed``). An
admin reads them in the club's languages and edits one: the text sent
replaces its texts in the club's languages and keeps the ones in languages
the club has removed (R30). A saved template is drawn with the labels it
was measured with (R14), so an edit reaches only templates saved after it.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.application_label import ApplicationLabel
from ..exceptions import ApplicationLabelNotFoundException
from ..utils import now_utc_ms
from .application_catalogue import JsonDict, labels
from .application_languages import check_written, club_languages, kept
from .application_seed import ensure_labels_seeded


async def club_labels(db: AsyncSession) -> JsonDict:
    """Every label, ``key → {language: text}``, in the catalogue's order,
    in every language stored."""
    await ensure_labels_seeded(db)
    rows = await db.execute(select(ApplicationLabel))
    stored = {row.key: row.text for row in rows.scalars()}
    return {key: stored[key] for key in labels() if key in stored}


class ApplicationLabelService:
    """Edit the club's labels."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def update(
        self, key: str, text: dict[str, str], updated_by: str
    ) -> ApplicationLabel:
        """Replace a label's text in the club's languages (R8a, R30)."""
        if key not in labels():
            raise ApplicationLabelNotFoundException(key)
        await ensure_labels_seeded(self.db)
        club = await club_languages(self.db)
        check_written(text, club)
        row = await self.db.get(ApplicationLabel, key)
        if row is None:
            raise ApplicationLabelNotFoundException(key)
        row.text = kept(text, row.text, club)
        row.updated_at = now_utc_ms()
        row.updated_by = updated_by
        await self.db.flush()
        await self.db.refresh(row)
        return row
