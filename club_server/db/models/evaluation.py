"""Evaluation models (#302, #535).

An evaluation is always about something — its scope, the organising idea of
`docs/evaluation_requirements.md`: one event, or (``event_id`` null) the
member in general. Its content is answers (``evaluation_answer.py``) to its
template's questions.
"""

from enum import Enum

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db.base import Base
from .evaluation_answer import EvaluationAnswer

__all__ = [
    "Evaluation",
    "EvaluationMediaLink",
    "EvaluationStatus",
]


class EvaluationStatus(str, Enum):
    """Evaluation lifecycle state (R14-R19)."""

    draft = "draft"
    saved = "saved"
    published = "published"


class Evaluation(Base):
    """A coach's assessment of one member, within one scope."""

    __tablename__ = "evaluations"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_for: Mapped[str] = mapped_column(
        String(50),
        ForeignKey(
            "users.username", ondelete="CASCADE", name="fk_evaluations_created_for"
        ),
        nullable=False,
    )
    # Never changes (R8).
    created_by: Mapped[str] = mapped_column(
        String(50),
        ForeignKey(
            "users.username", ondelete="CASCADE", name="fk_evaluations_created_by"
        ),
        nullable=False,
    )
    # Set by a transfer; null means the creator still owns it (R36).
    owner: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="SET NULL", name="fk_evaluations_owner"),
        nullable=True,
    )
    # Null = general scope (R1-R3). A period is any start and end, on either
    # scope; the occurrences it covers are derived, not stored (R4, R6).
    event_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE", name="fk_evaluations_event_id"),
        nullable=True,
    )
    period_start_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    period_end_utc: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Cleared on withdrawal (R20), so its presence means "currently published".
    published_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    answers: Mapped[list[EvaluationAnswer]] = relationship(
        EvaluationAnswer,
        lazy="selectin",
        order_by=EvaluationAnswer.item_id,
        cascade="all, delete-orphan",
    )

    @property
    def effective_owner(self) -> str:
        """The owner after a transfer, else the creator (R35)."""
        return self.owner or self.created_by

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        Index("idx_evaluations_created_for", "created_for"),
        Index("idx_evaluations_created_by", "created_by"),
        Index("idx_evaluations_owner", "owner"),
        Index("idx_evaluations_event", "event_id"),
        Index("idx_evaluations_status", "status"),
        Index("idx_evaluations_deleted_at", "deleted_at"),
    )


class EvaluationMediaLink(Base):
    """Evidence attached to an evaluation (R55, R56, R56a).

    Same shape as the four per-owner link tables in ``media_links.py``;
    kept here so the module stays detachable. The tag is the id of the
    question the evidence justifies.
    """

    __tablename__ = "evaluation_media"  # pyright: ignore[reportUnannotatedClassAttribute]

    evaluation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    media_uuid: Mapped[str] = mapped_column(
        Text, ForeignKey("media.uuid", ondelete="RESTRICT"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)
    metadata_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        Index("idx_evaluation_media_media_uuid", "media_uuid"),
    )
