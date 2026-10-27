"""Who an application's form is for: age categories and the preview member (#93).

Pure functions over the form's own documents (``application_document``):
ISO dates, snake-case keys. Ported from the prototype.
"""

from datetime import date
from typing import Any

from ..exceptions import (
    ApplicationCategoryNotFittingException,
    ApplicationCategoryRequiredException,
    ApplicationNotEligibleException,
)
from .application_catalogue import JsonDict

ADULT_AGE = 18
# The preview member's birthday inside a category, and without categories.
PREVIEW_BIRTH_MONTH, PREVIEW_BIRTH_DAY = 6, 15
PREVIEW_EARLY_MONTH, PREVIEW_EARLY_DAY = 3, 10
PREVIEW_AGE = 12
LAST_SAFE_DAY = 28


def fits(category: JsonDict, dob: date | None, gender: str | None) -> bool:
    """Whether a member falls in one age category (applications R11).

    A member with no date of birth fits none.
    """
    if dob is None:
        return False
    after = category.get("born_on_or_after")
    before = category.get("born_on_or_before")
    if after and dob < date.fromisoformat(after):
        return False
    if before and dob > date.fromisoformat(before):
        return False
    return not category.get("genders") or gender in category["genders"]


def fitting_categories(
    categories: list[JsonDict], dob: date | None, gender: str | None
) -> list[int]:
    """The indexes of every age category the member falls in (R11a)."""
    return [i for i, c in enumerate(categories) if fits(c, dob, gender)]


def matching_category(
    categories: list[JsonDict], dob: date | None, gender: str | None
) -> int | None:
    """The index of the first age category the member falls in, or ``None``."""
    found = fitting_categories(categories, dob, gender)
    return found[0] if found else None


def choose_category(
    categories: list[JsonDict],
    dob: date | None,
    gender: str | None,
    chosen: int | None,
) -> int:
    """The category ticked on a member's form (R11, R11a).

    Fits none → not eligible; ``chosen`` names a category they do not fit →
    refused; they fit several and chose none → asked to choose.
    """
    found = fitting_categories(categories, dob, gender)
    if not found:
        raise ApplicationNotEligibleException()
    if chosen is not None:
        if chosen not in found:
            raise ApplicationCategoryNotFittingException(chosen, found)
        return chosen
    if len(found) > 1:
        raise ApplicationCategoryRequiredException(found)
    return found[0]


def is_eligible(categories: list[JsonDict], member: JsonDict) -> bool:
    """Whether ``member`` may have the form: no categories, or one matches."""
    if not categories:
        return True
    born = member.get("date_of_birth")
    dob = date.fromisoformat(born) if born else None
    return matching_category(categories, dob, member.get("gender")) is not None


def is_minor(dob: date | None, today: date) -> bool:
    """Under 18 on ``today``; a member with no date of birth is not."""
    if dob is None:
        return False
    t = today
    return t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day)) < ADULT_AGE


# The member an admin's preview is drawn for: fictional but believable, every
# field filled the way a real member would fill it, with a guardian, so the
# admin sees every part of the form as members will.
PREVIEW_MEMBER: JsonDict = {
    "username": "demo.player",
    "first_name": "Arjun",
    "middle_name": "Vikram",
    "last_name": "Deshpande",
    "date_of_birth": None,  # set per template: inside the first age category
    "gender": "male",
    "address": {
        "addr_line1": "Flat 304, Sunshine Heights, Lane 5, Baner Road",
        "addr_line2": "Near Balewadi High Street",
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411045",
    },
    "phone": "+91 98765 43210",
    "email": "arjun.deshpande@example.com",
    "nationality": "Indian",
    "preferred_language": "Marathi",
    "membership_date": None,  # set per preview: a year before the preview day
    "club_district": "Pune",
    "preferred_position": None,  # set per preview: the club's first position
    "past_experience": "Inline skating for three years at the district level; "
    "played roller hockey in two state tournaments; one season of ice skating "
    "lessons at a local rink.",
    "chosen_category": None,  # set per template: the first category
    "guardian": {
        "name": "Vikram Deshpande",
        "relation": "Father",
        "phone": "+91 98765 01234",
    },
}


def preview_member(template: JsonDict, options: JsonDict, today: date) -> JsonDict:
    """The demo member, born well inside the template's first age category."""
    member: dict[str, Any] = dict(PREVIEW_MEMBER)
    cats: list[JsonDict] = template.get("age_categories") or []
    if cats:
        c = cats[0]
        after, before = c.get("born_on_or_after"), c.get("born_on_or_before")
        if after:
            # 15 June of the first year, kept inside the band (#100).
            first = date.fromisoformat(after)
            born = max(
                first.replace(month=PREVIEW_BIRTH_MONTH, day=PREVIEW_BIRTH_DAY), first
            )
            if before:
                born = min(born, date.fromisoformat(before))
        elif before:
            born = min(
                date.fromisoformat(before).replace(
                    month=PREVIEW_EARLY_MONTH, day=PREVIEW_EARLY_DAY
                ),
                date.fromisoformat(before),
            )
        else:
            born = date(
                today.year - PREVIEW_AGE, PREVIEW_BIRTH_MONTH, PREVIEW_BIRTH_DAY
            )
        member["date_of_birth"] = born.isoformat()
        member["chosen_category"] = 0  # the preview ticks the first (R19)
        if c.get("genders"):
            member["gender"] = c["genders"][0]
    else:
        member["date_of_birth"] = date(
            today.year - PREVIEW_AGE, PREVIEW_BIRTH_MONTH, PREVIEW_BIRTH_DAY
        ).isoformat()
    if member["gender"] == "female":
        member["first_name"], member["email"] = "Ananya", "ananya.deshpande@example.com"
    positions: list[JsonDict] = options.get("positions") or []
    if positions:
        member["preferred_position"] = positions[0]["value"]
    member["membership_date"] = date(
        today.year - 1, today.month, min(today.day, LAST_SAFE_DAY)
    ).isoformat()
    return member
