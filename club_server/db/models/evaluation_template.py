"""Evaluation template model (#302, #535).

A template is front matter — a name and a layout — plus its items
(``evaluation_template_item.py``). The items are the validation contract an
evaluation's answers are written against (`docs/evaluation_requirements.md`
R10, R12); the layout orders them and groups them into titled sections
(R12c).
"""

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base
from .evaluation_template_item import EvaluationTemplateItem


class EvaluationTemplate(Base):
    """A reusable set of questions an evaluation is written against."""

    __tablename__ = "evaluation_templates"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # RESTRICT, not CASCADE: a template is club material that other coaches'
    # evaluations use. Hard-deleting its creator hands it to the super admin
    # performing the delete (#490).
    created_by: Mapped[str] = mapped_column(
        String(50),
        ForeignKey(
            "users.username",
            ondelete="RESTRICT",
            name="fk_evaluation_templates_created_by",
        ),
        nullable=False,
    )
    # Ordered item ids and ``{"section": title, "items": [ids]}`` groups,
    # one level deep. Every item of the template appears exactly once.
    layout: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    items: Mapped[list[EvaluationTemplateItem]] = relationship(
        EvaluationTemplateItem,
        back_populates="template",
        lazy="selectin",
        order_by=EvaluationTemplateItem.id,
        cascade="all, delete-orphan",
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        Index("idx_evaluation_templates_deleted_at", "deleted_at"),
    )
