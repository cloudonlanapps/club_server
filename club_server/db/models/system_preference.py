from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class SystemPreference(Base):
    """Admin-managed key/value system settings.

    Generic store for cross-cutting configuration that admins can tune
    without a deploy. Values are JSONB so any JSON-serialisable shape is
    permitted; per-key validation lives in the service layer.
    """

    __tablename__ = "system_preferences"  # pyright: ignore[reportUnannotatedClassAttribute]

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="SET NULL"),
        nullable=True,
    )
