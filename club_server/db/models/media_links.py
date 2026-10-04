"""Per-owner link tables connecting ``media`` to owners (#162).

All four tables share the same shape; the only difference is the owner FK
column type and target table. The DB column ``metadata_value`` is exposed
to API clients as ``metadata`` (SQLAlchemy reserves the ``metadata``
attribute name on declarative classes).
"""

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class UserMediaLink(Base):
    __tablename__ = "user_media"  # pyright: ignore[reportUnannotatedClassAttribute]

    username: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="CASCADE"),
        nullable=False,
    )
    media_uuid: Mapped[str] = mapped_column(
        Text,
        ForeignKey("media.uuid", ondelete="RESTRICT"),
        nullable=False,
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("username", "tag", "media_uuid", name="pk_user_media"),
        Index("idx_user_media_media_uuid", "media_uuid"),
        Index("idx_user_media_username_tag", "username", "tag"),
    )


class EventMediaLink(Base):
    __tablename__ = "event_media"  # pyright: ignore[reportUnannotatedClassAttribute]

    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_uuid: Mapped[str] = mapped_column(
        Text,
        ForeignKey("media.uuid", ondelete="RESTRICT"),
        nullable=False,
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("event_id", "tag", "media_uuid", name="pk_event_media"),
        Index("idx_event_media_media_uuid", "media_uuid"),
        Index("idx_event_media_event_tag", "event_id", "tag"),
    )


class GroupMediaLink(Base):
    __tablename__ = "group_media"  # pyright: ignore[reportUnannotatedClassAttribute]

    group_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_uuid: Mapped[str] = mapped_column(
        Text,
        ForeignKey("media.uuid", ondelete="RESTRICT"),
        nullable=False,
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("group_id", "tag", "media_uuid", name="pk_group_media"),
        Index("idx_group_media_media_uuid", "media_uuid"),
        Index("idx_group_media_group_tag", "group_id", "tag"),
    )


class VenueMediaLink(Base):
    __tablename__ = "venue_media"  # pyright: ignore[reportUnannotatedClassAttribute]

    venue_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_uuid: Mapped[str] = mapped_column(
        Text,
        ForeignKey("media.uuid", ondelete="RESTRICT"),
        nullable=False,
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("venue_id", "tag", "media_uuid", name="pk_venue_media"),
        Index("idx_venue_media_media_uuid", "media_uuid"),
        Index("idx_venue_media_venue_tag", "venue_id", "tag"),
    )
