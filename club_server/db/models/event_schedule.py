from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base
from .event_schedule_coach import EventScheduleCoach

if TYPE_CHECKING:
    from .event import Event
    from .venue import Venue


class EventSchedule(Base):
    """One period of an event's timetable (``docs/event_schedule_model.md``).

    Schedules for one event are contiguous and non-overlapping, ordered by
    ``effective_from``; each one's ``effective_until`` equals the next one's
    ``effective_from``. ``effective_until`` is exclusive and holds the cutoff
    instant exactly — an occurrence belongs to this schedule iff
    ``effective_from <= t < effective_until``. ``NULL`` means still running.

    Termination is the absence of a next schedule, not a flag: a split closes
    one schedule and opens another at the same instant; a terminate or a camp
    cancel closes one and stops. Camps and one-offs have exactly one.

    People are referenced by username (#386): the organizer is a nullable FK
    that is cleared when the user is hard-deleted, and the coaches are
    ``EventScheduleCoach`` rows that go with the user.
    """

    __tablename__ = "event_schedules"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    effective_from: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    start_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    venue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("venues.id"), nullable=False
    )
    organizer_name: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey(
            "users.username",
            ondelete="SET NULL",
            name="fk_event_schedules_organizer",
        ),
        nullable=True,
    )
    sessions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    event: Mapped["Event"] = relationship("Event", back_populates="schedules")
    venue: Mapped["Venue"] = relationship(
        "Venue", foreign_keys=[venue_id], lazy="selectin"
    )
    coaches: Mapped[list[EventScheduleCoach]] = relationship(
        "EventScheduleCoach",
        back_populates="schedule",
        order_by="EventScheduleCoach.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_event_schedules_event", "event_id", "effective_from"),
        Index("idx_event_schedules_venue", "venue_id"),
        Index("idx_event_schedules_organizer", "organizer_name"),
    )

    @property
    def coach_names_list(self) -> list[str]:
        """The assigned coaches' usernames, in the order they were listed."""
        return [coach.username for coach in self.coaches]

    def set_coaches(self, usernames: list[str] | None) -> None:
        """Replace the coach assignments with ``usernames`` (order kept, no repeats)."""
        seen: set[str] = set()
        ordered = [u for u in (usernames or []) if not (u in seen or seen.add(u))]
        self.coaches = [
            EventScheduleCoach(username=username, position=position)
            for position, username in enumerate(ordered)
        ]
