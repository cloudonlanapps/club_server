from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .user import User


class CreditSessionCharge(Base):
    """One charge for one member at one occurrence (#294).

    The unique constraint mirrors ``attendance_records`` exactly, which is
    what makes "a session is never charged twice" (R83) a property of the
    schema rather than a rule the application has to remember. A single
    charge may be met from several accounts (R29), so uniqueness cannot
    live on the ledger entries; they hang off this row instead, which also
    makes the exact-source refund in R49 a lookup rather than a
    reconstruction.
    """

    __tablename__ = "credit_session_charges"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_time_utc: Mapped[int] = mapped_column(BigInteger, nullable=False)
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refunded_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        UniqueConstraint("event_id", "occurrence_time_utc", "membername"),
        Index("idx_credit_charges_member", "membername"),
        Index("idx_credit_charges_occurrence", "event_id", "occurrence_time_utc"),
    )

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[membername], lazy="selectin"
    )
