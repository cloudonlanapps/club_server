"""Pydantic schemas for the broadcasts surface (#51)."""

from typing import Any, ClassVar
from pydantic import ConfigDict, model_validator

from ..db.models.broadcast import Broadcast
from .common import CamelCaseModel


class BroadcastCreate(CamelCaseModel):
    """Admin-authored broadcast create request.

    ``audienceSelector`` is a single flat JSON object (see issue #51):
    ``{"kind": "all_users"}``, ``{"kind": "role", "role": "admin"|"coach"}``,
    ``{"kind": "group", "groupId": <id>}``,
    ``{"kind": "event_members", "eventId": <id>}``,
    ``{"kind": "event_staff", "eventId": <id>}``,
    ``{"kind": "users", "usernames": [...]}``.

    When ``email`` is true the broadcast is additionally delivered by email to
    recipients who have an address on file (#271). ``emailSubject`` and
    ``emailBody`` are required in that case and are used *only* for the email;
    the in-app feed always renders ``payload``. ``emailBody`` is authored as
    **markdown** and rendered to HTML server-side (#273).
    """

    audience_selector: dict[str, Any]
    payload: dict[str, Any]
    expires_at_utc: int | None = None
    email: bool = False
    email_subject: str | None = None
    email_body: str | None = None

    @model_validator(mode="after")
    def _require_email_content(self) -> "BroadcastCreate":
        if self.email and not (self.email_subject and self.email_body):
            raise ValueError(
                "emailSubject and emailBody are required when email is true"
            )
        return self


class BroadcastResponse(CamelCaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    id: int
    sender_username: str | None
    audience_selector: dict[str, Any]
    payload: dict[str, Any]
    sent_at_utc: int
    expires_at_utc: int | None
    status: str
    recipient_count: int

    @classmethod
    def from_model(cls, b: Broadcast, recipient_count: int) -> "BroadcastResponse":
        return cls(
            id=b.id,
            sender_username=b.sender_username,
            audience_selector=b.audience_selector,
            payload=b.payload,
            sent_at_utc=b.sent_at,
            expires_at_utc=b.expires_at,
            status=b.status,
            recipient_count=recipient_count,
        )


class BroadcastDetail(BroadcastResponse):
    """Detail view adds aggregate read/unread counters."""

    read_count: int
    unread_count: int


class BroadcastRecipient(CamelCaseModel):
    """One row in `/broadcasts/{id}/recipients`."""

    username: str
    is_read: bool
    created_at_utc: int
