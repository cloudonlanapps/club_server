"""Who an application's form is for: age categories and the preview member (#93).

Pure functions over the form's own documents (``application_document``):
ISO dates, snake-case keys. Ported from the prototype.
"""

from datetime import date
from typing import Any

from .application_catalogue import JsonDict

ADULT_AGE = 18
# The preview member's birthday inside a category, and without categories.
PREVIEW_BIRTH_MONTH, PREVIEW_BIRTH_DAY = 6, 15
PREVIEW_EARLY_MONTH, PREVIEW_EARLY_DAY = 3, 10
PREVIEW_AGE = 12
LAST_SAFE_DAY = 28


def matching_category(
    categories: list[JsonDict], dob: date | None, gender: str | None
) -> int | None:
    """The index of the first age category the member falls in, or ``None``.

    A member with no date of birth matches none (applications R11).
    """
    for i, c in enumerate(categories):
        after = c.get("born_on_or_after")
        before = c.get("born_on_or_before")
        if dob is None:
            break
        if after and dob < date.fromisoformat(after):
            continue
        if before and dob > date.fromisoformat(before):
            continue
        if c.get("genders") and gender not in c["genders"]:
            continue
        return i
    return None


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
            born = date.fromisoformat(after).replace(
                month=PREVIEW_BIRTH_MONTH, day=PREVIEW_BIRTH_DAY
            )
            if before and born > date.fromisoformat(before):
                born = date.fromisoformat(after)
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
