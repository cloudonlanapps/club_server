from typing import Any

from .common import CamelCaseModel


class AuditUserRef(CamelCaseModel):
    username: str
    full_name: str | None


class AuditLogRow(CamelCaseModel):
    """Single audit row.

    `resource` is intentionally typed as a free-form dict because its shape
    varies by `resource_type`:
      - generic: `{type, id, label}`
      - occurrence: `{type, eventId, eventTitle, occurrenceTimeUtc}`
      - unknown: `{type, id, label: null}`
    Keeping it a dict avoids Pydantic emitting irrelevant null fields.
    """

    id: int
    timestamp: int
    actor: AuditUserRef | None
    target: AuditUserRef | None
    action: str
    resource: dict[str, Any] | None
    details: dict[str, Any] | None
    summary: dict[str, str]
    """Per-language human-readable sentence, e.g. ``{"en": "..."}``. Rendered at
    query time (never stored); see ``services.audit_summary``."""


class AuditLogResponse(CamelCaseModel):
    total: int
    offset: int
    limit: int
    rows: list[AuditLogRow]
