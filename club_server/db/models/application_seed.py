"""What the server has seeded into the club's application data (#133).

One row per seeding (``library_texts``): its presence means the seeding ran,
so it never runs again and a seeded row the club deletes stays deleted. The
name is the primary key, so two workers racing to seed insert it once.
"""

from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class ApplicationSeed(Base):
    """A seeding that has run, and when."""

    __tablename__ = "application_seeds"  # pyright: ignore[reportUnannotatedClassAttribute]

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    seeded_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
