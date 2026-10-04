"""Inquiry schemas (#407)."""

from typing import Any, ClassVar, Literal

from pydantic import ConfigDict, EmailStr, Field

from ..db.models.inquiry import Inquiry
from .common import CamelCaseModel

MESSAGE_MAX = 2000

InquiryKind = Literal["contact", "interest"]


class InquirySubmission(CamelCaseModel):
    """The public form body (R23, R24, R26).

    ``token`` is the server-issued fill-time token; ``website`` is the
    honeypot, a field a human never fills.
    """

    kind: InquiryKind
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr = Field(..., max_length=320)
    phone: str | None = Field(None, max_length=32)
    message: str = Field(..., min_length=1, max_length=MESSAGE_MAX)
    extra: dict[str, Any] | None = None
    token: str = Field(..., min_length=1, max_length=200)
    website: str | None = Field(None, max_length=500)


class FormTokenResponse(CamelCaseModel):
    """A fill-time token for the public form."""

    token: str


class InquiryHandledUpdate(CamelCaseModel):
    """Mark an inquiry handled or reopen it."""

    handled: bool


class InquiryResponse(CamelCaseModel):
    """The admin inbox row; carries no source hash."""

    id: int
    kind: str
    name: str
    email: str
    phone: str | None
    message: str
    extra: dict[str, Any] | None
    created_at_utc: int
    handled_at: int | None
    handled_by: str | None

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, row: Inquiry) -> "InquiryResponse":
        return cls(
            id=row.id,
            kind=row.kind,
            name=row.name,
            email=row.email,
            phone=row.phone,
            message=row.message,
            extra=row.extra,
            created_at_utc=row.created_at,
            handled_at=row.handled_at,
            handled_by=row.handled_by,
        )
