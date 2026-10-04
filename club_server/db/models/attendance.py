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
    from .user import User


class AttendanceStatus(str, Enum):
    """Attendance status."""

    present = "present"
    absent = "absent"
    late = "late"
    on_leave = "onLeave"
    on_leave_requested = "onLeaveRequested"


class AttendanceRecord(Base):
    """Attendance record model matching local store schema exactly."""

    __tablename__ = "attendance_records"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_time_utc: Mapped[int] = mapped_column(BigInteger, nullable=False)
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    leave_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        UniqueConstraint("event_id", "occurrence_time_utc", "membername"),
        Index("idx_attendance_event_occurrence", "event_id", "occurrence_time_utc"),
    )

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[membername], lazy="selectin"
    )
