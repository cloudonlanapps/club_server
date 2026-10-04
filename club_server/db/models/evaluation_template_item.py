"""One question or info text of an evaluation template (#535).

The columns every item has are columns; the properties of its type (scale,
choices, comment area, evidence, …) are the ``element`` JSONB, holding
exactly the variant the API's discriminated union accepted
(`docs/evaluation_requirements.md` R12).
"""

from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base

if TYPE_CHECKING:
    from .evaluation_template import EvaluationTemplate


class EvaluationItemType(str, Enum):
    """The kinds of item a template holds. ``info`` asks nothing."""

    rating = "rating"
    yes_no = "yesNo"
    single_choice = "singleChoice"
    multiple_choice = "multipleChoice"
    number = "number"
    qa = "qa"
    info = "info"


class EvaluationTemplateItem(Base):
    """A question or info text, identified by its id alone."""

    __tablename__ = "evaluation_template_items"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # Markdown; null for ``info``, whose text lives in ``element``.
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    element: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set on a copy: the item it was first copied from, never a copy of a
    # copy, so comparison across templates is one join (R12b).
    origin_item_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("evaluation_template_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    template: Mapped["EvaluationTemplate"] = relationship(
        "EvaluationTemplate", back_populates="items"
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        Index("idx_evaluation_template_items_template_id", "template_id"),
        Index("idx_evaluation_template_items_origin", "origin_item_id"),
    )
