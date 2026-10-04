from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class AuditLog(Base):
    """Audit log model matching local store schema exactly."""

    __tablename__ = "audit_log"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute, reportAny]
        Index("idx_audit_actor", "actor_username"),
        Index("idx_audit_target", "target_username"),
        Index("idx_audit_timestamp", "timestamp"),
    )
