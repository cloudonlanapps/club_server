from typing import Any, ClassVar
from pydantic import ConfigDict

from ..db.models.notification import Notification, NotificationPref
from .common import CamelCaseModel


class NotificationResponse(CamelCaseModel):
    """Schema for notification response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    id: int
    username: str
    type: str
    channel: str
    payload: dict[str, Any]
    pending_action_type: str | None = None
    pending_action_id: int | None = None
    pending_action_key: str | None = None
    broadcast_id: int | None = None
    is_read: bool
    created_at_utc: int

    @classmethod
    def from_model(cls, notification: Notification) -> "NotificationResponse":
        """Create response from Notification model."""
        return cls(
            id=notification.id,
            username=notification.username,
            type=notification.type,
            channel=notification.channel,
            payload=notification.payload,
            pending_action_type=notification.pending_action_type,
            pending_action_id=notification.pending_action_id,
            pending_action_key=notification.pending_action_key,
            broadcast_id=notification.broadcast_id,
            is_read=bool(notification.is_read),
            created_at_utc=notification.created_at,
        )


class NotificationPrefResponse(CamelCaseModel):
    """Schema for notification preference response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    email_enabled: bool
    push_enabled: bool
    sms_enabled: bool

    @classmethod
    def from_model(cls, pref: NotificationPref) -> "NotificationPrefResponse":
        """Create response from NotificationPref model."""
        return cls(
            email_enabled=bool(pref.email_enabled),
            push_enabled=bool(pref.push_enabled),
            sms_enabled=bool(pref.sms_enabled),
        )


class NotificationPrefUpdate(CamelCaseModel):
    """Schema for updating notification preference."""

    email_enabled: bool | None = None
    push_enabled: bool | None = None
    sms_enabled: bool | None = None


class NotificationCreate(CamelCaseModel):
    """Schema for creating a notification (admin)."""

    username: str
    type: str
    channel: str
    payload: dict[str, Any]
    pending_action_type: str | None = None
    pending_action_id: int | None = None
    pending_action_key: str | None = None
