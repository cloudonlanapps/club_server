"""The club's labels: the application form's fixed wording (#133).

Seeded on first use from the label catalogue (``labels.json``), one row per
label, and from then on the club's own: an admin edits a label's text per
language. A label is never deleted; a label the catalogue gains later is
added on its next use, as written there. ``text`` is ``{language: text}``
in every language it was written in, the club's current ones or not.
"""

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class ApplicationLabel(Base):
    """One label: its catalogue key and its text per language."""

    __tablename__ = "application_labels"  # pyright: ignore[reportUnannotatedClassAttribute]

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    text: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # SET NULL: hard-deleting the admin who edited a label keeps it.
    updated_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey(
            "users.username",
            ondelete="SET NULL",
            name="fk_application_labels_updated_by",
        ),
        nullable=True,
    )
