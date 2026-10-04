from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .user import User


class Group(Base):
    """Group model matching local store schema exactly."""

    __tablename__ = "groups"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, default="manual", nullable=False)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)
    dob_on_or_after_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dob_on_or_before_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    members: Mapped[list["GroupMember"]] = relationship(
        "GroupMember", back_populates="group", lazy="selectin", passive_deletes=True
    )


class GroupMember(Base):
    """Group member junction table matching local store schema exactly."""

    __tablename__ = "group_members"  # pyright: ignore[reportUnannotatedClassAttribute]

    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), primary_key=True
    )

    group: Mapped["Group"] = relationship("Group", back_populates="members")
    user: Mapped["User"] = relationship("User", lazy="selectin")
