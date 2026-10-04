"""A departure's deferred credit disposition, as stored on the enrollment (#448).

When a member leaves while a session they are on is under way, the admin's
disposition is kept in ``Enrollment.pending_disposition`` and applied by the
sweep once that session has ended (credit R74a). The admin who stated it is
stored beside it, so the sweep applies it in their name (R82).
"""

import json

from ..schemas.credit import STORED_DISPOSITION
from ..schemas.credit import CreditDispositionRequest as CreditDisposition

PENDING_ACTOR_KEY = "actorUsername"
"""Who decided a deferred disposition, stored beside it in
``Enrollment.pending_disposition`` so the sweep can name them (R82)."""


def pending_disposition_json(disposition: CreditDisposition, actor: str | None) -> str:
    """Serialise a deferred disposition together with the admin who stated it."""
    data = disposition.model_dump(mode="json")
    data[PENDING_ACTOR_KEY] = actor
    return json.dumps(data)


def parse_pending_disposition(raw: str) -> tuple[CreditDisposition, str | None]:
    """Read back a deferred disposition and its actor.

    Rows deferred before the actor was stored carry none, and read back
    with no actor, as they were written.
    """
    data = json.loads(raw)
    actor = data.pop(PENDING_ACTOR_KEY, None)
    return (
        CreditDisposition.model_validate(data, context={STORED_DISPOSITION: True}),
        actor,
    )
