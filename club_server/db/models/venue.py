from sqlalchemy import BigInteger, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class Venue(Base):
    """Venue model matching local store schema exactly."""

    __tablename__ = "venues"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    map_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_featured: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
