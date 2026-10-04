from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class BroadcastStatus(str, Enum):
    """Lifecycle states of a broadcast row."""

    draft = "draft"
    sent = "sent"
    revoked = "revoked"


class Broadcast(Base):
    """Admin-authored fan-out message, with one fan-out row per recipient
    stored as ``Notification`` rows linked via ``notifications.broadcast_id``.

    The audience selector is a single flat JSONB object (see #51 issue
    body for the v1 selector grammar). Per-recipient read state is
    derived by aggregating the linked ``notifications`` rows.
    """

    __tablename__ = "broadcasts"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_username: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="SET NULL"), nullable=True
    )
    audience_selector: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sent_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=BroadcastStatus.sent.value
    )
