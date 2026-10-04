from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .event import Event


class OccurrenceOverride(Base):
    """Occurrence override model matching local store schema exactly."""

    __tablename__ = "occurrence_overrides"  # pyright: ignore[reportUnannotatedClassAttribute]

    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    occurrence_time: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    new_start_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_end_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_venue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_organizer_name: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey(
            "users.username",
            ondelete="SET NULL",
            name="fk_occurrence_overrides_new_organizer",
        ),
        nullable=True,
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optimistic locking (#430), on the event's terms (#292). An occurrence
    # with no row is at version 1, so a row starts at 2 and only goes up.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    updated_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey(
            "users.username",
            ondelete="SET NULL",
            name="fk_occurrence_overrides_updated_by",
        ),
        nullable=True,
    )

    event: Mapped["Event"] = relationship(
        "Event", foreign_keys=[event_id], lazy="selectin"
    )
