"""Admin curation of the public staff page (#332, public R1–R7).

One row per curated coach. Absence means "public, uncurated": the coach
appears if they consented, after the curated ones, by name. The row can
withhold or order a coach; it can never grant visibility.
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class PublicStaffListing(Base):
    """Position, guest and hidden marks for one coach on the staff page."""

    __tablename__ = "public_staff_listing"  # pyright: ignore[reportUnannotatedClassAttribute]

    username: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
