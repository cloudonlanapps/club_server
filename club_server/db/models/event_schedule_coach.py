from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .event_schedule import EventSchedule


class EventScheduleCoach(Base):
    """One coach assigned to one period of an event's timetable (#386).

    A coach assignment belongs to the schedule it was made for, the same
    way the venue and the organizer do, so "who coaches this event" reads
    through the current schedule. ``position`` keeps the order the coaches
    were listed in. Both FKs cascade: deleting the schedule or hard-deleting
    the user removes the assignment, never the other side.
    """

    __tablename__ = "event_schedule_coaches"  # pyright: ignore[reportUnannotatedClassAttribute]

    schedule_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("event_schedules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    username: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    schedule: Mapped["EventSchedule"] = relationship(
        "EventSchedule", back_populates="coaches"
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_event_schedule_coaches_username", "username"),
    )
