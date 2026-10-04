"""Whether the owner of a media link is soft-deleted (#517, media:R61).

Links on a soft-deleted owner stay listed and carry ``ownerDeleted``; on a
soft-deleted event, group or venue they are read-only until it is restored.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation
from ..db.models.event import Event
from ..db.models.group import Group
from ..db.models.user import User
from ..db.models.venue import Venue
from ..exceptions import OwnerDeletedException

# Owner type → (model, key column). The key is what a link stores.
_OWNER_MODELS: dict[str, tuple[Any, Any]] = {
    "user": (User, User.username),
    "event": (Event, Event.id),
    "group": (Group, Group.id),
    "venue": (Venue, Venue.id),
    "evaluation": (Evaluation, Evaluation.id),
}

# Owner types whose links may not be written while the owner is deleted.
READ_ONLY_WHEN_DELETED = frozenset({"event", "group", "venue"})


def _key(owner_type: str, owner_id: Any) -> Any:
    """The owner id as its key column stores it (the view reports text)."""
    return str(owner_id) if owner_type == "user" else int(owner_id)


async def deleted_owner_keys(
    db: AsyncSession, owners: Iterable[tuple[str, Any]]
) -> set[tuple[str, str]]:
    """The ``(owner_type, str(owner_id))`` pairs whose owner is soft-deleted."""
    by_type: dict[str, set[Any]] = {}
    for owner_type, owner_id in owners:
        if owner_type in _OWNER_MODELS:
            by_type.setdefault(owner_type, set()).add(_key(owner_type, owner_id))
    deleted: set[tuple[str, str]] = set()
    for owner_type, ids in by_type.items():
        model, key_col = _OWNER_MODELS[owner_type]
        rows = await db.execute(
            select(key_col).where(key_col.in_(ids), model.deleted_at.is_not(None))
        )
        deleted |= {(owner_type, str(key)) for key in rows.scalars().all()}
    return deleted


async def is_owner_deleted(db: AsyncSession, owner_type: str, owner_id: Any) -> bool:
    """True when the owner exists and is soft-deleted."""
    found = await deleted_owner_keys(db, [(owner_type, owner_id)])
    return (owner_type, str(owner_id)) in found


async def refuse_write_when_owner_deleted(
    db: AsyncSession, owner_type: str, owner_id: Any
) -> None:
    """Raise ``OwnerDeletedException`` for a write to a read-only owner's links."""
    if owner_type in READ_ONLY_WHEN_DELETED and await is_owner_deleted(
        db, owner_type, owner_id
    ):
        raise OwnerDeletedException(owner_type, owner_id)
