"""Schemas for /v1/media (#161, v2 media foundation)."""

import json
from typing import ClassVar

from pydantic import ConfigDict, Field

from ..db.models.media import Media
from .common import CamelCaseModel


class MediaRef(CamelCaseModel):
    """A media item as a client needs it to paint (#424, #426).

    Everywhere a media uuid is published, this goes instead. A bare uuid tells
    a client nothing: the download URL has no extension either, so a renderer
    that decides between a picture, a video and a document has nothing to read
    and gets it wrong.

    Three fields, and no fourth. [mime_type] picks the renderer — every stored
    row's type is one of ``image/*``, ``video/*`` or ``application/pdf``, so
    the coarser `mediaType` it would imply is not published beside it.
    [filename] is appended to the download URL, which the route ignores for
    lookup, so the URL ends in a real extension and a browser has a sensible
    name to save under.

    Per-variant facts are deliberately absent. `HEAD` on the download URL
    answers a variant's type, its size and whether it exists at all, for
    exactly the variant asked about — which a list of variant names embedded
    in every response could only approximate, and did so wrongly.
    """

    uuid: str
    mime_type: str
    filename: str

    @classmethod
    def from_model(cls, media: Media) -> "MediaRef":
        # Imported here: services.media imports this module for MediaResponse.
        from ..services.media import download_filename

        return cls(
            uuid=media.uuid,
            mime_type=media.mime_type,
            filename=download_filename(media),
        )


class MediaPatchRequest(CamelCaseModel):
    """Body for PATCH /v1/media/by_id/{id}.

    Only ``accessRoles`` is mutable. ``usageContext`` is intentionally omitted —
    link-table metadata (#162) replaces that concept.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    access_roles: list[str] | None = Field(default=None)


class MediaResponse(CamelCaseModel):
    """Schema for a media record.

    ``mime_type`` is the type of the **stored** file — what a download hands
    back — and ``filename`` is what to call it. ``original_mime_type`` and
    ``original_filename`` are the upload, kept because a PNG stored as WebP
    has two truthful answers and each field is named for the one it holds.

    ``media_type`` stays here, unlike on [MediaRef], because on a media record
    it is the row's own classification rather than a rendering hint (#426).
    """

    id: int
    uuid: str
    original_filename: str
    media_type: str
    mime_type: str
    original_mime_type: str
    filename: str
    file_size: int
    preserve_original: bool
    conversion_status: str
    conversion_error: str | None
    uploaded_by: str | None
    access_roles: list[str]
    is_encrypted: bool
    created_at_utc: int
    updated_at_utc: int
    deleted_at_utc: int | None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    @classmethod
    def from_model(cls, media: Media) -> "MediaResponse":
        from ..services.media import download_filename

        try:
            roles = json.loads(media.access_roles) if media.access_roles else ["public"]
        except (ValueError, TypeError):
            roles = ["public"]
        return cls(
            id=media.id,
            uuid=media.uuid,
            original_filename=media.original_filename,
            media_type=media.media_type,
            mime_type=media.mime_type,
            original_mime_type=media.original_mime_type,
            filename=download_filename(media),
            file_size=media.file_size,
            preserve_original=bool(media.preserve_original),
            conversion_status=media.conversion_status,
            conversion_error=media.conversion_error,
            uploaded_by=media.uploaded_by,
            access_roles=roles,
            is_encrypted=bool(media.is_encrypted),
            created_at_utc=media.created_at,
            updated_at_utc=media.updated_at,
            deleted_at_utc=media.deleted_at,
        )
