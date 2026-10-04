"""Schemas for per-owner media links (#162)."""

import re
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator

from .common import CamelCaseModel
from .media import MediaRef

TAG_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
METADATA_MAX_LEN = 2048

OwnerType = Literal["user", "event", "group", "venue"]


def _validate_tag(v: str) -> str:
    if not isinstance(v, str) or not TAG_PATTERN.match(v):
        raise ValueError("tag must match ^[A-Za-z0-9_-]{1,64}$")
    return v


def _validate_metadata(v: str | None) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("metadata must be a string")
    if len(v) > METADATA_MAX_LEN:
        raise ValueError(f"metadata length must be ≤{METADATA_MAX_LEN}")
    return v


class MediaLinkCreate(CamelCaseModel):
    """Body for POST /v1/<owner>/by_id/{id}/media."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    tag: str
    media_uuid: str = Field(min_length=1)
    metadata: str | None = None

    @field_validator("tag")
    @classmethod
    def _ck_tag(cls, v: str) -> str:
        return _validate_tag(v)

    @field_validator("metadata")
    @classmethod
    def _ck_meta(cls, v: str | None) -> str | None:
        return _validate_metadata(v)


class MediaLinkPatch(CamelCaseModel):
    """Body for PATCH /v1/<owner>/by_id/{id}/media/{tag}/{media_uuid}.

    Only ``metadata`` is mutable. Tag and ``mediaUuid`` form the link's
    identity — to change them, DELETE + POST.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    metadata: str | None = None

    @field_validator("metadata")
    @classmethod
    def _ck_meta(cls, v: str | None) -> str | None:
        return _validate_metadata(v)


class MediaLinkResponse(CamelCaseModel):
    """A single link row, with the media it points at.

    ``media`` is the same descriptor every other projection embeds, so the
    facts about a media item have one shape and one set of names (#426).
    """

    tag: str
    metadata: str | None
    media: MediaRef
    created_at_utc: int
    updated_at_utc: int


class MediaLinkReverseEntry(CamelCaseModel):
    """One row returned by GET /v1/media/by_id/{uuid}/links."""

    owner_type: OwnerType
    owner_id: str
    tag: str
    metadata: str | None
    created_at_utc: int
    updated_at_utc: int


class MediaLinkCrossEntry(CamelCaseModel):
    """One row returned by GET /v1/media/links (admin/coach search)."""

    media_uuid: str
    owner_type: OwnerType
    owner_id: str
    tag: str
    metadata: str | None
    media_type: str
    created_at_utc: int
