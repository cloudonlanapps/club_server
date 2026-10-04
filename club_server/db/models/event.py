from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base
from ...utils import now_utc_ms
from .event_schedule import EventSchedule

# The timetable lives on the schedule, never on the event. These names are
# accepted by ``Event(...)`` for the first schedule and exposed as properties
# reading the current one, so callers see one object with one id for life.
# ``coach_names`` is accepted as a list of usernames and becomes coach rows.
_SCHEDULE_FIELDS = frozenset(
    {
        "start_time",
        "end_time",
        "rrule",
        "venue_id",
        "organizer_name",
        "coach_names",
        "sessions",
        "until_time",
        "effective_from",
    }
)


class Event(Base):
    """An event: identity, eligibility and presentation.

    What changes over time — times, rule, venue, staffing, timetable — is a
    sequence of ``EventSchedule`` rows (``docs/event_schedule_model.md``).
    ``event_id`` therefore means one thing only, and enrollments, attendance,
    overrides and credit never move. The hybrid properties below read and
    write the **current** schedule (the last one) and, in SQL, correlate to
    it, so a query on ``Event.start_time`` asks about the current schedule.
    """

    __tablename__ = "events"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(Text, nullable=False)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)
    dob_on_or_after_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dob_on_or_before_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gallery_uris: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Basic marketing block (#409): what every club's site puts on a card.
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stamp: Mapped[str | None] = mapped_column(Text, nullable=True)
    highlights: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    includes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    credit_released_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Optimistic locking (#292): every mutation bumps ``version`` and
    # records who made it, so a stale client is told rather than overwritten.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="SET NULL", name="fk_events_updated_by"),
        nullable=True,
    )

    schedules: Mapped[list[EventSchedule]] = relationship(
        "EventSchedule",
        back_populates="event",
        order_by="EventSchedule.effective_from",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_events_type", "type"),
        Index("idx_events_visibility", "visibility"),
    )

    def __init__(self, **kwargs: Any) -> None:
        """Build an event and its first schedule from one set of keyword arguments.

        Every event has at least one schedule, so the timetable fields are
        accepted here and become schedule 1 rather than being columns of
        their own. ``effective_from`` defaults to ``start_time``.
        """
        schedule_kwargs = {
            k: kwargs.pop(k) for k in list(kwargs) if k in _SCHEDULE_FIELDS
        }
        super().__init__(**kwargs)
        if not schedule_kwargs:
            return
        now = now_utc_ms()
        start = schedule_kwargs["start_time"]
        schedule = EventSchedule(
            effective_from=schedule_kwargs.get("effective_from", start),
            effective_until=schedule_kwargs.get("until_time"),
            start_time=start,
            end_time=schedule_kwargs["end_time"],
            rrule=schedule_kwargs.get("rrule"),
            venue_id=schedule_kwargs["venue_id"],
            organizer_name=schedule_kwargs.get("organizer_name"),
            sessions=schedule_kwargs.get("sessions"),
            created_at=kwargs.get("created_at", now),
            updated_at=kwargs.get("updated_at", now),
        )
        schedule.set_coaches(schedule_kwargs.get("coach_names"))
        self.schedules.append(schedule)

    # --- the current schedule ---------------------------------------------

    def touch(self, actor: str | None, now: int | None = None) -> None:
        """Record a mutation: bump ``version``, stamp ``updated_at`` and ``updated_by``."""
        self.version = (self.version or 0) + 1
        self.updated_at = now if now is not None else now_utc_ms()
        self.updated_by = actor

    @property
    def current_schedule(self) -> EventSchedule:
        """The last schedule — the one that decides what happens from now on."""
        return self.schedules[-1]

    @property
    def first_schedule(self) -> EventSchedule:
        """The schedule the event started with."""
        return self.schedules[0]

    @property
    def coach_names_list(self) -> list[str]:
        """The current schedule's coaches, in the order they were listed."""
        return self.current_schedule.coach_names_list

    def set_coaches(self, usernames: list[str] | None) -> None:
        """Replace the current schedule's coach assignments."""
        self.current_schedule.set_coaches(usernames)

    # --- hybrids: instance reads/writes go to the current schedule; SQL
    # --- correlates to it. One definition per field, generated below.

    @staticmethod
    def _current(column: Any) -> Any:
        return (
            select(column)
            .where(EventSchedule.event_id == Event.id)
            .order_by(EventSchedule.effective_from.desc())
            .limit(1)
            .scalar_subquery()
        )

    @hybrid_property
    def start_time(self) -> int:
        return self.current_schedule.start_time

    @start_time.inplace.setter
    def _start_time_setter(self, value: int) -> None:
        schedule = self.current_schedule
        if schedule.effective_from == schedule.start_time:
            schedule.effective_from = value
        schedule.start_time = value

    @start_time.inplace.expression
    @classmethod
    def _start_time_expression(cls) -> Any:
        return cls._current(EventSchedule.start_time)

    @hybrid_property
    def end_time(self) -> int:
        return self.current_schedule.end_time

    @end_time.inplace.setter
    def _end_time_setter(self, value: int) -> None:
        self.current_schedule.end_time = value

    @end_time.inplace.expression
    @classmethod
    def _end_time_expression(cls) -> Any:
        return cls._current(EventSchedule.end_time)

    @hybrid_property
    def rrule(self) -> str | None:
        return self.current_schedule.rrule

    @rrule.inplace.setter
    def _rrule_setter(self, value: str | None) -> None:
        self.current_schedule.rrule = value

    @rrule.inplace.expression
    @classmethod
    def _rrule_expression(cls) -> Any:
        return cls._current(EventSchedule.rrule)

    @hybrid_property
    def venue_id(self) -> int:
        return self.current_schedule.venue_id

    @venue_id.inplace.setter
    def _venue_id_setter(self, value: int) -> None:
        self.current_schedule.venue_id = value

    @venue_id.inplace.expression
    @classmethod
    def _venue_id_expression(cls) -> Any:
        return cls._current(EventSchedule.venue_id)

    @hybrid_property
    def organizer_name(self) -> str | None:
        return self.current_schedule.organizer_name

    @organizer_name.inplace.setter
    def _organizer_name_setter(self, value: str | None) -> None:
        self.current_schedule.organizer_name = value

    @organizer_name.inplace.expression
    @classmethod
    def _organizer_name_expression(cls) -> Any:
        return cls._current(EventSchedule.organizer_name)

    @hybrid_property
    def sessions(self) -> str | None:
        return self.current_schedule.sessions

    @sessions.inplace.setter
    def _sessions_setter(self, value: str | None) -> None:
        self.current_schedule.sessions = value

    @sessions.inplace.expression
    @classmethod
    def _sessions_expression(cls) -> Any:
        return cls._current(EventSchedule.sessions)

    @hybrid_property
    def cutoff(self) -> int | None:
        """The instant the event stops running: the current schedule's
        ``effective_until`` (lifecycle L1). ``None`` while open-ended."""
        return self.current_schedule.effective_until

    @cutoff.inplace.setter
    def _cutoff_setter(self, value: int | None) -> None:
        self.current_schedule.effective_until = value

    @cutoff.inplace.expression
    @classmethod
    def _cutoff_expression(cls) -> Any:
        return cls._current(EventSchedule.effective_until)

    @hybrid_property
    def until_time(self) -> int | None:
        """The cutoff under the name the API has always used (``untilTimeUtc``)."""
        return self.current_schedule.effective_until

    @until_time.inplace.setter
    def _until_time_setter(self, value: int | None) -> None:
        self.current_schedule.effective_until = value

    @until_time.inplace.expression
    @classmethod
    def _until_time_expression(cls) -> Any:
        return cls._current(EventSchedule.effective_until)
