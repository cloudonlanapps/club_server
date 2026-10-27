"""A published application's stored drawing (#128, applications R25).

The form of a published template drawn once with every label and box but
no member in it, and ``places``: where each member value and tick goes
(page, position, box width, lines, line height). A download writes the
member's values over it. It is kept for the ``publish_id`` and the club
logo (``logo_key``, the SHA-256 of its bytes, "" for none) it was drawn
with, and drawn again when either changes. It is derived data: dropped on
unpublish, deleted with its template.
"""

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class ApplicationDrawing(Base):
    """One published template's blank form and the places of its values."""

    __tablename__ = "application_drawings"  # pyright: ignore[reportUnannotatedClassAttribute]

    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
            name="fk_application_drawings_application_id",
        ),
        primary_key=True,
    )
    publish_id: Mapped[str] = mapped_column(String(36), nullable=False)
    logo_key: Mapped[str] = mapped_column(String(64), nullable=False)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    places: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    drawn_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
