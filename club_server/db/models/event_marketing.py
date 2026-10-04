"""Extended marketing block for an event (#410, marketing R5–R12).

One row per event, only on deployments that run the Event Marketing
module. Lists and nested shapes are JSON text validated on write. Currency
is stored for a future second currency and never exposed.
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base

DEFAULT_CURRENCY = "INR"


class EventMarketing(Base):
    """Commercial detail the public site shows on one event."""

    __tablename__ = "event_marketing"  # pyright: ignore[reportUnannotatedClassAttribute]

    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    duration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligibility_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligibility_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_deadline_utc: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    has_open_slots: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    urgency_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fee: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default=DEFAULT_CURRENCY
    )
    fee_structure: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    package_offers: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    offers: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    club_membership: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    facilities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
