"""Apply the basic marketing fields to an event (#409, marketing R1–R3).

One place for create, update and correction, so the four fields cannot
drift between the three paths. Lists are stored as JSON text.
"""

from typing import Any

from ..db.models.event import Event
from ..schemas.common import ChangeLog
from ..schemas.event_marketing import BASIC_MARKETING_FIELDS
from .schedule_writes import encode_names


def apply_basic_marketing(
    event: Event,
    changes: ChangeLog | None,
    fields_set: set[str],
    values: dict[str, Any],
) -> None:
    """Write every basic field named in ``fields_set``; ``None`` clears it."""
    for name in BASIC_MARKETING_FIELDS:
        if name not in fields_set:
            continue
        raw = values.get(name)
        stored = encode_names(raw) if name in ("highlights", "includes") else raw
        if changes is not None:
            changes.add(name, getattr(event, name), stored)
        setattr(event, name, stored)
