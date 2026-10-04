from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class UserReviewRequest(Base):
    """Audit-style row recording an admin's request that a pending user
    revisit their registration (see #122). At most one row per user may
    be unresolved at a time, enforced by a partial unique index.
    """

    __tablename__ = "user_review_requests"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.username"),
        nullable=False,
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resolved_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("users.username"),
        nullable=True,
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_user_review_requests_username", "username"),
        Index(
            "user_review_requests_active_uq",
            "username",
            unique=True,
            postgresql_where=(resolved_at.is_(None)),
        ),
    )
