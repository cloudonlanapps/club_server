"""The form's own documents, read from the database (#93).

The PDF code works on the prototype's plain documents — snake-case keys,
ISO dates — so the drawing stays as it was settled. This module turns an
``Application`` row, a ``User`` and the club's profile options into them.
"""

from datetime import date
from typing import Any

from ..club_calendar import day_to_date
from ..db.models.application import Application
from ..db.models.user import User
from ..schemas.common import Address
from ..schemas.extended_profile import Guardian, ProfileOptions
from .application_catalogue import JsonDict

DATE_FACT = "date"
RANGE_FACT = "date_range"


def _iso(day_ms: int | None) -> str | None:
    return day_to_date(day_ms).isoformat() if day_ms is not None else None


def _fact(stored: JsonDict) -> JsonDict:
    fact = dict(stored)
    if fact["type"] == DATE_FACT:
        fact["value"] = _iso(fact["value"])
    if fact["type"] == RANGE_FACT:
        fact["from"], fact["to"] = _iso(fact["from"]), _iso(fact["to"])
    return fact


def _category(stored: JsonDict) -> JsonDict:
    category: JsonDict = {"label": stored["label"]}
    after = _iso(stored.get("bornOnOrAfterUtc"))
    before = _iso(stored.get("bornOnOrBeforeUtc"))
    if after:
        category["born_on_or_after"] = after
    if before:
        category["born_on_or_before"] = before
    if stored.get("genders"):
        category["genders"] = stored["genders"]
    return category


def template_document(application: Application) -> JsonDict:
    """The template as the form reads it, its references not yet resolved."""
    facts: dict[str, Any] = application.facts
    return {
        "status": application.status,
        "languages": list(application.languages),
        "facts": {name: _fact(f) for name, f in facts.items()},
        "organisation": application.organisation,
        "title": application.title,
        "subtitle": application.subtitle,
        "header": list(application.header),
        "age_categories": [_category(c) for c in application.age_categories],
        "selections": list(application.selections),
        "declaration": application.declaration,
        "form_footer": application.form_footer,
        "terms": list(application.terms),
        "terms_notes": list(application.terms_notes),
        "label_overrides": dict(application.label_overrides),
    }


def _guardian(user: User) -> JsonDict | None:
    if not user.guardian:
        return None
    guardian = Guardian.model_validate_json(user.guardian).model_dump()
    return guardian if any(guardian.values()) else None


def member_document(user: User) -> JsonDict:
    """The member's profile as the form prints it."""
    address = (
        Address.model_validate_json(user.address).model_dump() if user.address else None
    )
    born: date | None = (
        day_to_date(user.date_of_birth) if user.date_of_birth is not None else None
    )
    return {
        "username": user.username,
        "first_name": user.first_name,
        "middle_name": user.middle_name,
        "last_name": user.last_name,
        "date_of_birth": born.isoformat() if born else None,
        "gender": user.gender,
        "address": address,
        "phone": user.phone,
        "email": user.email,
        "nationality": user.nationality,
        "preferred_language": user.preferred_language,
        "membership_date": _iso(user.membership_date),
        "club_district": user.club_district,
        "preferred_position": user.preferred_position,
        "past_experience": user.past_experience,
        "guardian": _guardian(user),
    }


def options_document(options: ProfileOptions) -> JsonDict:
    """The club's positions as the form ticks them."""
    return options.model_dump(mode="json")
