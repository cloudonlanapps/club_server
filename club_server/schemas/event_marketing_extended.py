"""Extended marketing block schemas (#410).

``EventMarketingWrite`` is the whole-row PUT body: every field optional,
omitted means cleared. ``currency`` is deliberately not a field. The two
responses differ only in how the event is named: integer id for staff,
public id for the website.
"""

import json
from typing import ClassVar

from pydantic import ConfigDict, Field

from ..db.models.event_marketing import EventMarketing
from ..utils import generate_event_public_id
from .common import CamelCaseModel

TEXT_MAX = 500
LIST_MAX = 20


class FeeItem(CamelCaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    amount: int = Field(..., ge=0)
    period: str | None = Field(None, max_length=50)


class PackageOffer(CamelCaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    price: int = Field(..., ge=0)
    description: str | None = Field(None, max_length=TEXT_MAX)
    features: list[str] | None = Field(None, max_length=LIST_MAX)


class PromotionalOffer(CamelCaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=TEXT_MAX)
    valid_until_utc: int | None = None


class ClubMembership(CamelCaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=TEXT_MAX)
    benefits: list[str] | None = Field(None, max_length=LIST_MAX)


class Facility(CamelCaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=TEXT_MAX)
    icon_name: str | None = Field(None, max_length=50)


class EventMarketingWrite(CamelCaseModel):
    """Whole-row replace body (R6, R8)."""

    duration_text: str | None = Field(None, max_length=TEXT_MAX)
    schedule_text: str | None = Field(None, max_length=TEXT_MAX)
    eligibility_text: str | None = Field(None, max_length=TEXT_MAX)
    eligibility_note: str | None = Field(None, max_length=TEXT_MAX)
    registration_deadline_utc: int | None = None
    has_open_slots: bool | None = None
    urgency_text: str | None = Field(None, max_length=200)
    contact_number: str | None = Field(None, max_length=32)
    fee: int | None = Field(None, ge=0)
    fee_structure: list[FeeItem] | None = Field(None, max_length=LIST_MAX)
    package_offers: list[PackageOffer] | None = Field(None, max_length=LIST_MAX)
    offers: list[PromotionalOffer] | None = Field(None, max_length=LIST_MAX)
    club_membership: ClubMembership | None = None
    facilities: list[Facility] | None = Field(None, max_length=LIST_MAX)


def _load(raw: str | None):
    return json.loads(raw) if raw else None


class _MarketingFields(CamelCaseModel):
    duration_text: str | None
    schedule_text: str | None
    eligibility_text: str | None
    eligibility_note: str | None
    registration_deadline_utc: int | None
    has_open_slots: bool | None
    urgency_text: str | None
    contact_number: str | None
    fee: int | None
    fee_structure: list[FeeItem] | None
    package_offers: list[PackageOffer] | None
    offers: list[PromotionalOffer] | None
    club_membership: ClubMembership | None
    facilities: list[Facility] | None

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    @classmethod
    def _fields(cls, row: EventMarketing) -> dict:
        return {
            "duration_text": row.duration_text,
            "schedule_text": row.schedule_text,
            "eligibility_text": row.eligibility_text,
            "eligibility_note": row.eligibility_note,
            "registration_deadline_utc": row.registration_deadline_utc,
            "has_open_slots": row.has_open_slots,
            "urgency_text": row.urgency_text,
            "contact_number": row.contact_number,
            "fee": row.fee,
            "fee_structure": _load(row.fee_structure),
            "package_offers": _load(row.package_offers),
            "offers": _load(row.offers),
            "club_membership": _load(row.club_membership),
            "facilities": _load(row.facilities),
        }


class EventMarketingResponse(_MarketingFields):
    """Staff view, keyed by the integer event id."""

    event_id: int

    @classmethod
    def from_model(cls, row: EventMarketing) -> "EventMarketingResponse":
        return cls(event_id=row.event_id, **cls._fields(row))


class PublicEventMarketingResponse(_MarketingFields):
    """Website view, keyed by the event's public id (R10)."""

    public_id: str

    @classmethod
    def from_model(cls, row: EventMarketing) -> "PublicEventMarketingResponse":
        return cls(public_id=generate_event_public_id(row.event_id), **cls._fields(row))
