from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class Media(Base):
    """v2 media record (#161). Parallel to ``uploaded_media``.

    Differences from the v1 ``UploadedMedia`` model:
    - ``uploaded_by`` is ``ON DELETE SET NULL`` and nullable — uploads
      outlive uploaders (#157).
    - No ``usage_context``; per-owner link tables (#162) replace it.
    """

    __tablename__ = "media"  # pyright: ignore[reportUnannotatedClassAttribute]

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    original_mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    original_extension: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    preserve_original: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversion_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending"
    )
    conversion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversion_params: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("users.username", ondelete="SET NULL"),
        nullable=True,
    )
    access_roles: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default='["public"]',
    )
    is_encrypted: Mapped[bool] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    encryption_version: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    encryption_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        Index("idx_media_media_type", "media_type"),
        Index("idx_media_conversion_status", "conversion_status"),
        Index("idx_media_uploaded_by", "uploaded_by"),
        Index("idx_media_deleted_at", "deleted_at"),
    )
