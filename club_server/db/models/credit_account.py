from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .event import Event
    from .user import User

ACCOUNT_CODE_LENGTH = 8


class CreditAccountKind(StrEnum):
    """Whether an account is usable for one programme or for any (R2)."""

    event = "event"
    general = "general"


class CreditAccountState(StrEnum):
    """Derived lifecycle state of an account.

    Ordered: ``closed`` wins, then ``expired``, then ``empty``, then
    ``usable``. Never stored — computed from the account's own columns
    and its ledger balance.
    """

    usable = "usable"
    empty = "empty"
    expired = "expired"
    closed = "closed"


class CreditAccount(Base):
    """A named pot of credits belonging to exactly one member (#294).

    Accounts are the unit of ownership: a member does not have a balance,
    they have accounts. The balance itself is never stored — it is the sum
    of the account's ledger entries (R4), which is what makes the ledger's
    immutability structural rather than a rule to remember.

    ``event_id`` holds the **chain root** of a programme's continuation
    chain, never a mid-chain successor (R7), so splitting or extending a
    programme cannot strand its credits.
    """

    __tablename__ = "credit_accounts"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(ACCOUNT_CODE_LENGTH), nullable=False, unique=True
    )
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    is_trial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_until: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_by: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_credit_accounts_member", "membername"),
        Index("idx_credit_accounts_event", "event_id"),
        Index("idx_credit_accounts_opened", "opened_at"),
    )

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[membername], lazy="selectin"
    )
    event: Mapped["Event | None"] = relationship(
        "Event", foreign_keys=[event_id], lazy="selectin"
    )

    @property
    def kind(self) -> str:
        """Whether this account is bound to one programme or usable for any."""
        return (
            CreditAccountKind.general
            if self.event_id is None
            else CreditAccountKind.event
        ).value

    def state(self, balance: int, now_utc: int) -> str:
        """Derived state for ``balance`` at ``now_utc`` (R11, R28)."""
        if self.closed_at is not None:
            return CreditAccountState.closed.value
        if not self.valid_from <= now_utc <= self.valid_until:
            return CreditAccountState.expired.value
        if balance <= 0:
            return CreditAccountState.empty.value
        return CreditAccountState.usable.value

    def is_usable(self, balance: int, now_utc: int) -> bool:
        """Whether credit may be spent from this account right now (R28)."""
        return self.state(balance, now_utc) == CreditAccountState.usable.value
