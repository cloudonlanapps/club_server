"""An occurrence's own version (#430, lifecycle L23).

Changes to a single occurrence live in ``occurrence_overrides``, not on the
event row, so the event's version (#292, L22) does not protect them. Each
row carries its own: an occurrence with no row is at version 1, the first
change writes the row at 2, and every later change bumps it. A version never
goes backwards, so a row whose changes are all undone is kept, not deleted.
"""

from ..db.models.occurrence_override import OccurrenceOverride
from ..exceptions import StaleOccurrenceVersionException
from ..utils import now_utc_ms

# An occurrence nobody has changed has no override row and is at version 1;
# the first change writes the row at 2 (lifecycle L23).
UNCHANGED_OCCURRENCE_VERSION = 1
# The status of a row kept after undo-cancel left nothing overridden, so the
# occurrence's version never goes backwards (L23b).
SCHEDULED_OVERRIDE_STATUS = "scheduled"


def check_occurrence_version(
    override: OccurrenceOverride | None, expected_version: int
) -> None:
    """L23a: refuse a change carrying a version the occurrence has moved past."""
    if override is None:
        current, updated_at, updated_by = UNCHANGED_OCCURRENCE_VERSION, None, None
    else:
        current, updated_at, updated_by = (
            override.version,
            override.updated_at,
            override.updated_by,
        )
    if current != expected_version:
        raise StaleOccurrenceVersionException(current, updated_at, updated_by)


def stamp_override(override: OccurrenceOverride, actor: str | None) -> None:
    """L23: a change to an existing row bumps its version and records who made it."""
    override.version = (override.version or UNCHANGED_OCCURRENCE_VERSION) + 1
    override.updated_at = now_utc_ms()
    override.updated_by = actor


def new_override(
    event_id: int, occurrence_time: int, actor: str | None, **fields: object
) -> OccurrenceOverride:
    """L23: the first change to an occurrence writes its row at version 2."""
    return OccurrenceOverride(
        event_id=event_id,
        occurrence_time=occurrence_time,
        version=UNCHANGED_OCCURRENCE_VERSION + 1,
        updated_at=now_utc_ms(),
        updated_by=actor,
        **fields,
    )


def clear_override(override: OccurrenceOverride, actor: str | None) -> None:
    """L23b: undo every change on the row but keep it, one version further on."""
    override.status = SCHEDULED_OVERRIDE_STATUS
    override.new_start_time = None
    override.new_end_time = None
    override.new_venue_id = None
    override.new_organizer_name = None
    override.cancel_reason = None
    stamp_override(override, actor)
