from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .user import User


class Notification(Base):
    """Notification row — fact payload, optional pending-action pointer."""

    __tablename__ = "notifications"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pending_action_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_action_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_action_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    broadcast_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[username], lazy="selectin"
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_notifications_user", "username"),
        Index(
            "idx_notifications_pending_action",
            "pending_action_type",
            "pending_action_id",
        ),
        Index(
            "idx_notifications_pending_action_key",
            "pending_action_type",
            "pending_action_key",
        ),
    )


class NotificationPref(Base):
    """Notification preferences model matching local store schema exactly."""

    __tablename__ = "notification_prefs"  # pyright: ignore[reportUnannotatedClassAttribute]

    username: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), primary_key=True
    )
    email_enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    push_enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sms_enabled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[username], lazy="selectin"
    )
