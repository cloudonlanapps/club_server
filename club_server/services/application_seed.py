"""Seeding the built-in clauses into the club's library, once (#133, applications R7b).

The first use of the library copies every clause of ``clauses.json`` into
``application_library_texts`` as an ordinary club text, keeping its key,
kind, heading and text in every language the file has. The seeding records
itself in ``application_seeds``: from then on nothing is seeded, so a seeded
text the club deletes stays deleted and an edited one keeps its edit.

Two workers may race on a fresh database: the marker is inserted with
``ON CONFLICT DO NOTHING`` in the transaction that seeds, so the second
waits for the first, finds the marker taken and seeds nothing.
"""

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.application_library_text import ApplicationLibraryText
from ..db.models.application_seed import ApplicationSeed
from ..utils import now_utc_ms
from .application_catalogue import library

LIBRARY_TEXTS = "library_texts"
ENGLISH = "en"
NAME_MAX = 100


def seeded_name(key: str, clause: dict[str, Any]) -> str:
    """A seeded text's name: its English heading, else its key read aloud
    (``header.training_camp`` → "Header: training camp")."""
    heading: dict[str, str] = clause.get("heading") or {}
    if heading.get(ENGLISH):
        return heading[ENGLISH][:NAME_MAX]
    kind, _, rest = key.partition(".")
    readable = rest.replace("_", " ") or kind
    return f"{kind.capitalize()}: {readable}"[:NAME_MAX]


async def claim_seed(db: AsyncSession, name: str) -> bool:
    """Record that seeding ``name`` runs now; False when it already has."""
    if await db.get(ApplicationSeed, name) is not None:
        return False
    claimed = await db.execute(
        insert(ApplicationSeed)
        .values(name=name, seeded_at=now_utc_ms())
        .on_conflict_do_nothing(index_elements=[ApplicationSeed.name])
        .returning(ApplicationSeed.name)
    )
    return claimed.scalar_one_or_none() is not None


async def ensure_library_seeded(db: AsyncSession) -> None:
    """Copy the built-in clauses into the club's library, the first time only."""
    if not await claim_seed(db, LIBRARY_TEXTS):
        return
    now = now_utc_ms()
    clauses: dict[str, dict[str, Any]] = library()["clauses"]
    db.add_all(
        ApplicationLibraryText(
            key=key,
            kind=clause["kind"],
            name=seeded_name(key, clause),
            heading=clause.get("heading"),
            text=clause["text"],
            created_by=None,
            created_at=now,
            updated_at=now,
        )
        for key, clause in clauses.items()
    )
    await db.flush()
