"""Read-side service for the audit log.

Resolves foreign-key ids on each row to human-readable names so the client
can render the audit history without per-row follow-up requests. Resolution
ignores `deleted_at` — historical names remain visible even after the
referent is soft-deleted (an audit log records what happened in the past).
"""

import json
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from ..db.models.audit_log import AuditLog
from ..db.models.event import Event
from ..db.models.group import Group
from ..db.models.user import User
from ..db.models.venue import Venue
from ..schemas.audit_log import (
    AuditLogResponse,
    AuditLogRow,
    AuditUserRef,
)
from .audit_summary import SummaryContext, render_summaries

MIN_LIMIT = 1
MAX_LIMIT = 200
DEFAULT_LIMIT = 50

# Entity types whose audit rows can be widened to related rows via ``verbose``.
_SCOPED_ENTITY_TYPES = {"event", "group", "venue"}

_DETAIL_USERNAME_KEYS = {
    "username": "full_name",
    "target_username": "target_full_name",
}
_DETAIL_EVENT_KEY = "event_id"
_DETAIL_GROUP_KEY = "group_id"
_DETAIL_VENUE_KEY = "venue_id"


def _clamp_limit(limit: int) -> int:
    if limit < MIN_LIMIT:
        return MIN_LIMIT
    if limit > MAX_LIMIT:
        return MAX_LIMIT
    return limit


def _full_name(user: User) -> str | None:
    parts = [user.first_name, user.last_name]
    name = " ".join(p for p in parts if p)
    return name or None


def _parse_occurrence_resource_id(raw: str) -> tuple[int, int] | None:
    """Parse an occurrence composite `<eventId>:<occurrenceTimeUtc>`.

    Returns `(event_id, occurrence_time_utc)` or `None` if the input does not
    look like a composite (in which case it falls back to generic handling).
    """
    if ":" not in raw:
        return None
    left, _, right = raw.partition(":")
    if not left.isdigit() or not right.lstrip("-").isdigit():
        return None
    return int(left), int(right)


def _resource_scope_filter(
    resource_type: str, resource_id: str, verbose: int
) -> ColumnElement[bool]:
    """Build the entity-scope predicate for one resource, honouring ``verbose``.

    - ``verbose >= 1``: the entity's own rows (exact ``resource_type``/id).
    - ``verbose >= 2``: also its media-link rows (``resource_id`` =
      ``<owner_id>:<tag>:<media_uuid>``) and tag-deletion rows (``resource_id`` =
      ``<owner_id>:<tag>``), both matched by the ``<id>:`` prefix.
    - ``verbose >= 3`` (events only): also the event's occurrence rows, whose
      composite ``resource_id`` is ``<event_id>:<occurrence_time_utc>``.
    """
    clauses: list[ColumnElement[bool]] = [
        and_(
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == resource_id,
        )
    ]
    if resource_type in _SCOPED_ENTITY_TYPES:
        prefix = f"{resource_id}:"
        if verbose >= 2:
            clauses.append(
                and_(
                    AuditLog.resource_type.in_(
                        [f"{resource_type}_media_link", f"{resource_type}_media_tag"]
                    ),
                    AuditLog.resource_id.like(f"{prefix}%"),
                )
            )
        if verbose >= 3 and resource_type == "event":
            clauses.append(
                and_(
                    AuditLog.resource_type == "occurrence",
                    AuditLog.resource_id.like(f"{prefix}%"),
                )
            )
    return or_(*clauses)


def _maybe_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)
    return None


class AuditLogQueryService:
    """Read-only query helper for the audit log endpoint."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def query(
        self,
        *,
        offset: int = 0,
        limit: int = DEFAULT_LIMIT,
        actor: str | None = None,
        action: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
        username: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        verbose: int = 1,
    ) -> AuditLogResponse:
        """Query the audit log.

        ``username`` scopes to a single user, matching rows where they are
        *either* the actor or the target (``verbose`` does not affect it).

        ``resource_type``/``resource_id`` scope to a single entity. ``verbose``
        widens that scope: 1 = the entity's own rows only (default), 2 = also
        its media-link rows, 3 = also its occurrence rows (events only). See
        ``_resource_scope_filter``.
        """
        offset = max(0, offset)
        limit = _clamp_limit(limit)

        filters: list[ColumnElement[bool]] = []
        if actor is not None:
            filters.append(AuditLog.actor_username == actor)
        if action is not None:
            filters.append(AuditLog.action == action)
        if from_ts is not None:
            filters.append(AuditLog.timestamp >= from_ts)
        if to_ts is not None:
            filters.append(AuditLog.timestamp <= to_ts)
        if username is not None:
            filters.append(
                or_(
                    AuditLog.actor_username == username,
                    AuditLog.target_username == username,
                )
            )
        if resource_type is not None and resource_id is not None:
            filters.append(_resource_scope_filter(resource_type, resource_id, verbose))
        elif resource_type is not None:
            filters.append(AuditLog.resource_type == resource_type)
        elif resource_id is not None:
            filters.append(AuditLog.resource_id == resource_id)

        count_stmt = select(func.count()).select_from(AuditLog)
        for f in filters:
            count_stmt = count_stmt.where(f)
        total = int((await self.db.execute(count_stmt)).scalar_one())

        rows_stmt = (
            select(AuditLog)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        for f in filters:
            rows_stmt = rows_stmt.where(f)
        result = await self.db.execute(rows_stmt)
        raw_rows: Sequence[AuditLog] = result.scalars().all()

        parsed_details: list[dict[str, Any] | None] = [
            _safe_parse_json(r.details) for r in raw_rows
        ]

        usernames, event_ids, group_ids, venue_ids = self._collect_ids(
            raw_rows, parsed_details
        )

        user_names = await self._load_user_names(usernames)
        event_titles = await self._load_event_titles(event_ids)
        group_names = await self._load_group_names(group_ids)
        venue_names = await self._load_venue_names(venue_ids)

        rows: list[AuditLogRow] = []
        for raw, details in zip(raw_rows, parsed_details, strict=True):
            resource = _resolve_resource(
                raw.resource_type,
                raw.resource_id,
                user_names=user_names,
                event_titles=event_titles,
                group_names=group_names,
                venue_names=venue_names,
            )
            enriched = _enrich_details(
                details,
                user_names=user_names,
                event_titles=event_titles,
                group_names=group_names,
                venue_names=venue_names,
            )
            rows.append(
                AuditLogRow(
                    id=raw.id,
                    timestamp=raw.timestamp,
                    actor=_user_ref(raw.actor_username, user_names),
                    target=_user_ref(raw.target_username, user_names),
                    action=raw.action,
                    resource=resource,
                    details=enriched,
                    summary=render_summaries(
                        raw.action,
                        _summary_context(raw, resource, enriched, user_names),
                    ),
                )
            )

        return AuditLogResponse(total=total, offset=offset, limit=limit, rows=rows)

    def _collect_ids(
        self,
        raw_rows: Sequence[AuditLog],
        parsed_details: list[dict[str, Any] | None],
    ) -> tuple[set[str], set[int], set[int], set[int]]:
        usernames: set[str] = set()
        event_ids: set[int] = set()
        group_ids: set[int] = set()
        venue_ids: set[int] = set()

        for raw, details in zip(raw_rows, parsed_details, strict=True):
            if raw.actor_username:
                usernames.add(raw.actor_username)
            if raw.target_username:
                usernames.add(raw.target_username)
            self._collect_from_resource(
                raw.resource_type,
                raw.resource_id,
                event_ids=event_ids,
                group_ids=group_ids,
                venue_ids=venue_ids,
            )
            if details:
                for key, value in details.items():
                    if key in _DETAIL_USERNAME_KEYS and isinstance(value, str):
                        usernames.add(value)
                    elif key == _DETAIL_EVENT_KEY and isinstance(value, int):
                        event_ids.add(value)
                    elif key == _DETAIL_GROUP_KEY and isinstance(value, int):
                        group_ids.add(value)
                    elif key == _DETAIL_VENUE_KEY and isinstance(value, int):
                        venue_ids.add(value)

        return usernames, event_ids, group_ids, venue_ids

    @staticmethod
    def _collect_from_resource(
        resource_type: str | None,
        resource_id: str | None,
        *,
        event_ids: set[int],
        group_ids: set[int],
        venue_ids: set[int],
    ) -> None:
        if resource_id is None:
            return
        if resource_type == "occurrence":
            parsed = _parse_occurrence_resource_id(resource_id)
            if parsed is not None:
                event_ids.add(parsed[0])
            return
        as_int = _maybe_int(resource_id)
        if as_int is None:
            return
        if resource_type == "event":
            event_ids.add(as_int)
        elif resource_type == "group":
            group_ids.add(as_int)
        elif resource_type == "venue":
            venue_ids.add(as_int)

    async def _load_user_names(self, usernames: set[str]) -> dict[str, str | None]:
        if not usernames:
            return {}
        stmt = select(User).where(User.username.in_(usernames))
        result = await self.db.execute(stmt)
        users = result.scalars().all()
        return {u.username: _full_name(u) for u in users}

    async def _load_event_titles(self, event_ids: set[int]) -> dict[int, str | None]:
        if not event_ids:
            return {}
        stmt = select(Event.id, Event.title).where(Event.id.in_(event_ids))
        result = await self.db.execute(stmt)
        return {row.id: row.title for row in result}

    async def _load_group_names(self, group_ids: set[int]) -> dict[int, str | None]:
        if not group_ids:
            return {}
        stmt = select(Group.id, Group.name).where(Group.id.in_(group_ids))
        result = await self.db.execute(stmt)
        return {row.id: row.name for row in result}

    async def _load_venue_names(self, venue_ids: set[int]) -> dict[int, str | None]:
        if not venue_ids:
            return {}
        stmt = select(Venue.id, Venue.name).where(Venue.id.in_(venue_ids))
        result = await self.db.execute(stmt)
        return {row.id: row.name for row in result}


def _safe_parse_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast(dict[str, Any], parsed)


def _display_name(
    username: str | None, user_names: dict[str, str | None]
) -> str | None:
    if not username:
        return None
    return user_names.get(username) or username


def _summary_context(
    raw: AuditLog,
    resource: dict[str, Any] | None,
    details: dict[str, Any] | None,
    user_names: dict[str, str | None],
) -> SummaryContext:
    """Assemble the render-ready context for one row.

    ``resource_label`` is the human label the summary should use: the parent
    event's title for occurrence rows, otherwise the resolved ``label``.
    """
    resource_label: str | None = None
    resource_type: str | None = None
    if resource:
        resource_type = resource.get("type")
        if resource_type == "occurrence":
            resource_label = resource.get("eventTitle")
        else:
            resource_label = resource.get("label")
    return SummaryContext(
        actor=_display_name(raw.actor_username, user_names) or "Someone",
        target=_display_name(raw.target_username, user_names),
        resource_label=resource_label,
        resource_type=resource_type,
        details=details or {},
        timestamp_ms=raw.timestamp,
    )


def _user_ref(
    username: str | None, user_names: dict[str, str | None]
) -> AuditUserRef | None:
    if not username:
        return None
    return AuditUserRef(
        username=username,
        full_name=user_names.get(username),
    )


def _resolve_resource(
    resource_type: str | None,
    resource_id: str | None,
    *,
    user_names: dict[str, str | None],
    event_titles: dict[int, str | None],
    group_names: dict[int, str | None],
    venue_names: dict[int, str | None],
) -> dict[str, Any] | None:
    if resource_type is None and resource_id is None:
        return None

    if resource_type == "occurrence" and resource_id is not None:
        parsed = _parse_occurrence_resource_id(resource_id)
        if parsed is not None:
            event_id, occurrence_time = parsed
            return {
                "type": "occurrence",
                "eventId": event_id,
                "eventTitle": event_titles.get(event_id),
                "occurrenceTimeUtc": occurrence_time,
            }

    as_int = _maybe_int(resource_id)

    if resource_type == "event" and as_int is not None:
        return {"type": "event", "id": as_int, "label": event_titles.get(as_int)}
    if resource_type == "group" and as_int is not None:
        return {"type": "group", "id": as_int, "label": group_names.get(as_int)}
    if resource_type == "venue" and as_int is not None:
        return {"type": "venue", "id": as_int, "label": venue_names.get(as_int)}

    return {
        "type": resource_type or "unknown",
        "id": as_int if as_int is not None else resource_id,
        "label": None,
    }


def _enrich_details(
    details: dict[str, Any] | None,
    *,
    user_names: dict[str, str | None],
    event_titles: dict[int, str | None],
    group_names: dict[int, str | None],
    venue_names: dict[int, str | None],
) -> dict[str, Any] | None:
    if not details:
        return details

    enriched: dict[str, Any] = dict(details)
    for src, dst in _DETAIL_USERNAME_KEYS.items():
        value = enriched.get(src)
        if isinstance(value, str):
            enriched[dst] = user_names.get(value)
    event_value = enriched.get(_DETAIL_EVENT_KEY)
    if isinstance(event_value, int):
        enriched["event_title"] = event_titles.get(event_value)
    group_value = enriched.get(_DETAIL_GROUP_KEY)
    if isinstance(group_value, int):
        enriched["group_name"] = group_names.get(group_value)
    venue_value = enriched.get(_DETAIL_VENUE_KEY)
    if isinstance(venue_value, int):
        enriched["venue_name"] = venue_names.get(venue_value)
    return enriched
