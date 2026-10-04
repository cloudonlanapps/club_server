from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class AbsenceStreakWarning(Base):
    """One absence streak a member has been warned about (#478).

    A streak is identified by its member and the occurrence time of its
    first absence. The scan dedupes on this row rather than on the
    notification it sent, so the notification retention sweep cannot make
    an old streak warn again.
    """

    __tablename__ = "absence_streak_warnings"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    membername: Mapped[str] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    streak_start_utc: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warned_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        UniqueConstraint("membername", "streak_start_utc"),
    )
