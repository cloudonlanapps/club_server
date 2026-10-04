from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class CreditEntryType(StrEnum):
    """Every distinct kind of movement recorded in the credit ledger.

    ``validity_extended`` carries an amount of zero: a change to an
    account's window is not a movement of credit, but it belongs on the
    member's statement (R81).
    """

    grant = "grant"
    grant_reversal = "grantReversal"
    session_deduction = "sessionDeduction"
    session_refund = "sessionRefund"
    penalty = "penalty"
    transfer_out = "transferOut"
    transfer_in = "transferIn"
    validity_extended = "validityExtended"


class CreditEntry(Base):
    """One movement of credit into or out of one account (#294).

    The ledger is append-only: entries are never modified and never
    deleted (R80). A correction is a new entry that offsets an earlier
    one, linked by ``offsets_entry_id``.
    """

    __tablename__ = "credit_entries"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("credit_accounts.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    occurrence_time_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    charge_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("credit_session_charges.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_username: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    offsets_entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("credit_entries.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_credit_entries_account", "account_id"),
        Index("idx_credit_entries_charge", "charge_id"),
        Index("idx_credit_entries_created", "created_at"),
    )
