"""Service for the admin-managed system_preferences key/value store (#57)."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.system_preference import SystemPreference
from ..exceptions import SystemPreferenceNotFoundException
from ..schemas.system_preference import SystemPreferenceResponse
from ..utils import now_utc_ms
from .audit import AuditService
from .audit_actions import AuditAction


# Default values applied when a key is absent from the table. Acts as a
# safety net for fresh test DBs (where the seed migration may not have
# run) and as a single source of truth for sweep defaults.
DEFAULTS: dict[str, Any] = {
    "notification_info_retention_days": 90,
}

# Longest rendering of a value kept on a preference write's audit row;
# club_info and the like are summarised rather than copied whole (#525).
AUDIT_VALUE_MAX_CHARS = 200
_ELLIPSIS = "…"


def audit_text(value: Any) -> str:
    """A value as compact JSON, cut to ``AUDIT_VALUE_MAX_CHARS``."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= AUDIT_VALUE_MAX_CHARS:
        return text
    return text[: AUDIT_VALUE_MAX_CHARS - len(_ELLIPSIS)] + _ELLIPSIS


class SystemPreferenceService:
    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def list_all(self) -> list[SystemPreference]:
        result = await self.db.execute(
            select(SystemPreference).order_by(SystemPreference.key)
        )
        return list(result.scalars().all())

    async def read(self, key: str) -> SystemPreferenceResponse:
        """A preference as it is in force.

        A key never written reads its server default, or null when it has
        none, with no update time or writer: none is invented (#518).
        """
        row = await self._fetch(key)
        if row is None:
            return SystemPreferenceResponse(
                key=key,
                value=DEFAULTS.get(key),
                updated_at_utc=None,
                updated_by=None,
            )
        return self.to_response(row)

    async def read_all(self) -> list[SystemPreferenceResponse]:
        """Every stored preference plus each defaulted key never written,
        ordered by key."""
        items = [self.to_response(row) for row in await self.list_all()]
        stored = {item.key for item in items}
        items.extend(
            SystemPreferenceResponse(
                key=key, value=value, updated_at_utc=None, updated_by=None
            )
            for key, value in DEFAULTS.items()
            if key not in stored
        )
        return sorted(items, key=lambda item: item.key)

    @staticmethod
    def to_response(row: SystemPreference) -> SystemPreferenceResponse:
        """The response shape of a stored preference."""
        return SystemPreferenceResponse(
            key=row.key,
            value=row.value,
            updated_at_utc=row.updated_at,
            updated_by=row.updated_by,
        )

    async def get_value(self, key: str) -> Any:
        """Return the stored value, or the default if the key is absent."""
        row = await self._fetch(key)
        if row is None:
            if key in DEFAULTS:
                return DEFAULTS[key]
            raise SystemPreferenceNotFoundException(key)
        return row.value

    async def write(
        self, key: str, value: Any, actor_username: str, ip_address: str | None
    ) -> SystemPreference:
        """Store a value and audit the write with the key and the previous
        and new values; the previous is null for a key never written
        (platform R7, #525)."""
        previous = await self._fetch(key)
        previous_text = None if previous is None else audit_text(previous.value)
        row = await self.upsert(key, value, actor_username)
        await AuditService(self.db).log(
            actor_username=actor_username,
            action=AuditAction.UPDATE_SYSTEM_PREFERENCE,
            resource_type="system_preference",
            resource_id=key,
            details={
                "key": key,
                "previousValue": previous_text,
                "newValue": audit_text(value),
            },
            ip_address=ip_address,
        )
        return row

    async def upsert(
        self, key: str, value: Any, actor_username: str
    ) -> SystemPreference:
        row = await self._fetch(key)
        now = now_utc_ms()
        if row is None:
            row = SystemPreference(
                key=key,
                value=value,
                updated_at=now,
                updated_by=actor_username,
            )
            self.db.add(row)
        else:
            row.value = value
            row.updated_at = now
            row.updated_by = actor_username
        await self.db.flush()
        return row

    async def _fetch(self, key: str) -> SystemPreference | None:
        result = await self.db.execute(
            select(SystemPreference).where(SystemPreference.key == key)
        )
        return result.scalar_one_or_none()
