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
    from .group import Group


class JoinRequestStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class GroupJoinRequest(Base):
    """A user's request to join a group."""

    __tablename__ = "group_join_requests"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decided_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    group: Mapped["Group"] = relationship("Group", lazy="joined")

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_join_request_group", "group_id"),
        Index("idx_join_request_username", "username"),
        UniqueConstraint(
            "group_id",
            "username",
            name="uq_group_join_request_group_user",
        ),
    )
