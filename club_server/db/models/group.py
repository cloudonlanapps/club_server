from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...age_eligibility import EligibilityWindow, age_window, decode_age
from ...club_calendar import club_today
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
    # Age band (#16): JSON ``{years, months, days}`` per bound, NULL for none.
    # The window of birth dates is worked out from these on a reference day.
    min_age: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_age: Mapped[str | None] = mapped_column(Text, nullable=True)
    strict_age: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    members: Mapped[list["GroupMember"]] = relationship(
        "GroupMember", back_populates="group", lazy="selectin", passive_deletes=True
    )

    def window_on(self, reference_day_utc: int) -> EligibilityWindow:
        """The birth dates the group's age band admits on a given day."""
        return age_window(
            decode_age(self.min_age),
            decode_age(self.max_age),
            bool(self.strict_age),
            reference_day_utc,
        )

    @property
    def eligibility_window(self) -> EligibilityWindow:
        """The birth dates the group's age band admits today (eligibility R4)."""
        return self.window_on(club_today())


class GroupMember(Base):
    """Group member junction table matching local store schema exactly."""

    __tablename__ = "group_members"  # pyright: ignore[reportUnannotatedClassAttribute]

    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), primary_key=True
    )
    # When the daily scan told the admins this member no longer meets the
    # group's criteria (#17); NULL while they match. It is what makes the
    # notice go out once, and again only after they have matched in between.
    ineligible_reported_at: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    group: Mapped["Group"] = relationship("Group", back_populates="members")
    user: Mapped["User"] = relationship("User", lazy="selectin")
