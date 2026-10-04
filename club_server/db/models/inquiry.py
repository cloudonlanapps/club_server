"""Inbound submissions from people who are not users (#407, public R23–R30).

A contact-form message or an "I'm interested" registration. PII from
non-users: kept for a bounded time, never audited on submission, hard
deleted by admins.
"""

from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base

INQUIRY_KINDS = ("contact", "interest")


class Inquiry(Base):
    """One submission from the public website."""

    __tablename__ = "inquiries"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Salted hash of the client address, for dedupe; never the raw address.
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    handled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    handled_by: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("users.username", ondelete="SET NULL"), nullable=True
    )
