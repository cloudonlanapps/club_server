from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class RevokedSession(Base):
    """A login session ended by logout (#510).

    Every token carries the ``sid`` of the session its login began, and
    ``/auth/refresh`` passes it on, so one row refuses the session's access
    and refresh tokens alike. A row is needed only while a token of the
    session could still verify; ``expires_at`` says when that ends.
    """

    __tablename__ = "revoked_sessions"  # pyright: ignore[reportUnannotatedClassAttribute]

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="CASCADE"),
        nullable=False,
    )
    revoked_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_revoked_sessions_expires_at", "expires_at"),
    )
