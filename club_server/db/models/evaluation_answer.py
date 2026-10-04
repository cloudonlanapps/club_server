"""Answers on an evaluation (#535).

One row per answered question: a value of the question's kind, a coach
note, or both (R9). A multiple-choice answer's selections are rows of
their own, so they stay queryable.
"""

from sqlalchemy import Double, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base


class EvaluationAnswerChoice(Base):
    """One selected choice value of a multiple-choice answer."""

    __tablename__ = "evaluation_answer_choices"  # pyright: ignore[reportUnannotatedClassAttribute]

    answer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_answers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    value: Mapped[str] = mapped_column(Text, primary_key=True)


class EvaluationAnswer(Base):
    """The answer to one question of one evaluation."""

    __tablename__ = "evaluation_answers"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluations.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_template_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # A rating, Yes / No as 1 / 0, or a number.
    value_num: Mapped[float | None] = mapped_column(Double, nullable=True)
    # A single choice's value, or a Q & A answer in markdown.
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    coach_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    choices: Mapped[list[EvaluationAnswerChoice]] = relationship(
        EvaluationAnswerChoice,
        lazy="selectin",
        order_by=EvaluationAnswerChoice.value,
        cascade="all, delete-orphan",
    )

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        Index(
            "uq_evaluation_answers_evaluation_item",
            "evaluation_id",
            "item_id",
            unique=True,
        ),
        Index("idx_evaluation_answers_item_value", "item_id", "value_num"),
    )
