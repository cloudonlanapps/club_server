from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .event import Event
    from .user import User


class EnrollmentStatus(str, Enum):
    """Enrollment status following state machine."""

    invited = "invited"
    requested = "requested"
    accepted = "accepted"
    rejected = "rejected"
    assigned = "assigned"
    assigned_trial = "assignedTrial"
    withdrawn = "withdrawn"
    withdraw_requested = "withdrawRequested"
    declined = "declined"
    removed = "removed"


class Enrollment(Base):
    """Enrollment model matching local store schema exactly."""

    __tablename__ = "enrollments"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    is_trial: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    previous_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enrolled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    withdrawn_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # A departure with an occurrence still under way settles later (#378,
    # programme R29d): the disposition the admin stated, as JSON, and the
    # instant after which the sweep may apply it.
    pending_disposition: Mapped[str | None] = mapped_column(Text, nullable=True)
    settle_after_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        UniqueConstraint("membername", "event_id"),
        Index("idx_enrollments_event", "event_id"),
        Index("idx_enrollments_member", "membername"),
    )

    event: Mapped["Event"] = relationship(
        "Event", foreign_keys=[event_id], lazy="selectin"
    )
    user: Mapped["User"] = relationship(
        "User", foreign_keys=[membername], lazy="selectin"
    )
