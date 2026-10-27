"""Application template model (#93, `docs/applications_requirements.md`).

An admin-defined template from which a member downloads their own pre-filled
application form as a PDF. The multilingual texts sit in JSON columns in the
API's shapes (``schemas/application.py``), so no further table is needed.
``layout`` is set on save: the layout's name, page 1's spread with and
without the guardian block, and frozen copies of the profile options it was
measured with (#97) and of the library's formats and choices its facts were
printed with (#126). ``publish_id`` is a new UUID on each publish, kept
after an unpublish so the next publish can audit it as the previous one.
"""

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class Application(Base):
    """An application template: draft, saved or published; soft-deleted."""

    __tablename__ = "applications"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    languages: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    organisation: Mapped[Any] = mapped_column(JSONB, nullable=True)
    title: Mapped[Any] = mapped_column(JSONB, nullable=True)
    subtitle: Mapped[Any] = mapped_column(JSONB, nullable=True)
    header: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    age_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    selections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    declaration: Mapped[Any] = mapped_column(JSONB, nullable=True)
    form_footer: Mapped[Any] = mapped_column(JSONB, nullable=True)
    terms: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    terms_notes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    label_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    layout: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    publish_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # SET NULL: an application is club material; hard-deleting the admin who
    # created it keeps it.
    created_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey(
            "users.username", ondelete="SET NULL", name="fk_applications_created_by"
        ),
        nullable=True,
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        Index("idx_applications_status", "status"),
        Index("idx_applications_deleted_at", "deleted_at"),
    )
