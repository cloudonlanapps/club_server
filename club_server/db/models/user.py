from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .staff_listing import PublicStaffListing


class UserStatus(str, Enum):
    """User account status."""

    registered = "registered"
    pending = "pending"
    active = "active"
    blocked = "blocked"
    left = "left"


class Role(str, Enum):
    """User roles.

    The super admin is the ``User.is_super_admin`` flag, changed only by
    handover, never a role (#514).
    """

    admin = "admin"
    coach = "coach"


class Gender(str, Enum):
    """User gender."""

    male = "male"
    female = "female"
    other = "other"
    prefer_not_to_say = "prefer_not_to_say"


class User(Base):
    """User account model matching local store schema exactly."""

    __tablename__ = "users"  # pyright: ignore[reportUnannotatedClassAttribute]

    username: Mapped[str] = mapped_column(String(50), primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    middle_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_name_publicly: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_of_birth: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    medical_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    emergency_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    achievements: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public_profile: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="registered", nullable=False)
    is_super_admin: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    roles: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    last_login_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Set on every password change or reset; earlier tokens are refused (#461).
    password_changed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Admin curation of the public staff page (#332); absent = uncurated.
    staff_listing: Mapped["PublicStaffListing | None"] = relationship(
        "PublicStaffListing",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Partial unique index enforcing case-insensitive email uniqueness among
    # live accounts. Scoped to non-deleted rows so a soft-deleted user frees
    # their email for reuse, matching `deleted_at IS NULL` lookup semantics.
    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_users_status", "status"),
        Index(
            "uq_users_email_active",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL AND deleted_at IS NULL"),
        ),
    )
