import json
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.audit_log import AuditLog
from ..schemas.common import FieldValue
from ..utils import now_utc_ms
from .audit_actions import AuditAction

AuditDetails = Mapping[str, FieldValue | Mapping[str, FieldValue]]


class AuditService:
    """Service for logging audit events."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def log(
        self,
        actor_username: str,
        action: AuditAction,
        target_username: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: AuditDetails | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log an audit event."""
        details_json = None
        if details:
            details_json = json.dumps(details)
        if ip_address and details_json:
            details_data = json.loads(details_json)
            details_data["ip_address"] = ip_address
            details_json = json.dumps(details_data)
        elif ip_address:
            details_json = json.dumps({"ip_address": ip_address})

        entry = AuditLog(
            timestamp=now_utc_ms(),
            actor_username=actor_username,
            action=action.value,
            target_username=target_username,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details_json,
        )
        self.db.add(entry)
        await self.db.flush()
