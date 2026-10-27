"""A club's own library text for application templates (#126).

Club-wide: one library offered in every template. The built-in clauses are
seeded into it once (#133) and keep their clause's ``key``; a text the club
writes has none. The texts sit in JSON columns in the API's shape
(``{language: text}``), gaps as written.
"""

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class ApplicationLibraryText(Base):
    """A club text: its kind, a short name, a heading (a rule's) and its text."""

    __tablename__ = "application_library_texts"  # pyright: ignore[reportUnannotatedClassAttribute]
    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        UniqueConstraint("key", name="uq_application_library_texts_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The built-in clause a seeded text came from (``rule.matches``); unique
    # where set, null for a text the club wrote.
    key: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    heading: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    text: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # SET NULL: a library text is club material; hard-deleting the admin who
    # wrote it keeps it.
    created_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey(
            "users.username",
            ondelete="SET NULL",
            name="fk_application_library_texts_created_by",
        ),
        nullable=True,
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
