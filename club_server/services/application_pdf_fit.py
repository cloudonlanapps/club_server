"""Fitting the application form to its pages (#93, applications R16–R18).

The server measures page 1 before drawing it and uses the first layout
whose page 1 fits (``LAYOUTS``); page 1 is then spread to fill the page.
The layout depends on the template alone — every answer is fitted to its
lines and the guardian block prints on every form (#128) — so it is decided
when a template is saved, measured for a member whose every field is too
long, and stored with one spread. Ported from the prototype.
"""

import io
from typing import Any
from datetime import date

from PIL import Image

from ..exceptions import ApplicationDoesNotFitException
from .application_catalogue import JsonDict
from .application_pdf_builder import ApplicationFormBuilder
from .application_pdf_layout import BOTTOM, LAYOUTS, Layout

# Spread leaves this much below page 1's last line.
SPREAD_SLACK = 2
# Marks a layout measured with the guardian block, its note and the
# guardian's signature on every form: one spread (#128). A layout saved
# before has two, measured without the note, and is spread again on drawing.
ONE_SPREAD = "one_spread"


def _placeholder_logo() -> bytes:
    """A logo stand-in: the stored layout must fit with a logo present."""
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(out, format="PNG")
    return out.getvalue()


# A missing logo only shortens the header, so measuring with one is the
# worst case.
WORST_CASE_LOGO = _placeholder_logo()

# Every field filled, each too long for its lines. Answers are fitted to their
# lines, so the layout depends on the template alone; this member only proves
# that.
WORST_CASE_MEMBER: JsonDict = {
    "username": "w" * 30,
    "first_name": "Wwwwwwwwwwww",
    "middle_name": "Wwwwwwwwwwww",
    "last_name": "Wwwwwwwwwwwwwwww",
    "date_of_birth": "2015-01-01",
    "gender": "male",
    "address": {
        "addr_line1": "Flat 1204, Building B-12, Some Long Society Name Phase 2, Near The Big Landmark",
        "addr_line2": "Some Long Road Name, Locality Name, Another Area Name, Yet Another Area",
        "city": "Pimpri-Chinchwad",
        "state": "Maharashtra",
        "pincode": "411057",
    },
    "phone": "+91 98220 12345",
    "email": "a.very.long.email.address@example.com",
    "nationality": "Indian",
    "preferred_languages": ["Marathi"],
    "membership_date": "2026-01-01",
    "club_district": "Pune",
    "preferred_positions": ["Forward"],
    "past_experience": "w " * 300,
    "guardian": {
        "name": "Wwwwwwwwwwww Wwwwwwwwwwwwwwww",
        "relation": "Father",
        "phone": "+91 98220 54321",
    },
}


def worst_case_member(options: JsonDict) -> JsonDict:
    """The member a template is measured for: every field at its longest,
    every club position chosen and every club language listed (#106)."""
    member = dict(WORST_CASE_MEMBER)
    positions: list[JsonDict] = options.get("positions") or []
    # A language is {code, name} since #133, a name in options frozen before.
    entries: list[Any] = list(options.get("languages") or [])
    languages: list[str] = [
        str(e["name"]) if isinstance(e, dict) else str(e)  # pyright: ignore[reportUnknownArgumentType]
        for e in entries
    ]
    member["preferred_positions"] = [p["value"] for p in positions]
    member["preferred_languages"] = (
        languages or WORST_CASE_MEMBER["preferred_languages"]
    )
    return member


def choose_layout(
    template: JsonDict,
    member: JsonDict,
    labels: JsonDict,
    options: JsonDict,
    logo: bytes | None,
    today: date,
    worst: bool = False,
) -> tuple[Layout, float, float] | None:
    """The first layout whose page 1 fits, the spare height for each gap of its
    page 1, and the room left before spreading; None when none fits."""
    for layout in LAYOUTS:
        b = ApplicationFormBuilder(
            template,
            member,
            labels,
            options,
            logo,
            today,
            layout,
            measure=True,
            worst=worst,
        )
        _ = b.build()
        room = BOTTOM - (b.page1_end or 0)
        if room >= 0:
            return layout, (room - SPREAD_SLACK) / max(b.gaps, 1), room
    return None


def spread_of(
    template: JsonDict,
    labels: JsonDict,
    options: JsonDict,
    today: date,
    layout: Layout,
) -> float:
    """Page 1's spread in ``layout``, measured as on save: the spare height
    for each of its gaps (negative when page 1 overflows)."""
    b = ApplicationFormBuilder(
        template,
        worst_case_member(options),
        labels,
        options,
        WORST_CASE_LOGO,
        today,
        layout,
        measure=True,
        worst=True,
    )
    _ = b.build()
    return (BOTTOM - (b.page1_end or 0) - SPREAD_SLACK) / max(b.gaps, 1)


def check(
    template: JsonDict, labels: JsonDict, options: JsonDict, today: date
) -> JsonDict:
    """Decide the template's layout, as on save: the first layout that fits
    and the spread of its page 1. Every form prints the guardian block, so
    there is one spread; the API's two keys carry it both (#128).

    Raises ``ApplicationDoesNotFitException`` with the overflow when no
    layout fits (R17).
    """
    logo = WORST_CASE_LOGO
    worst = worst_case_member(options)
    chosen = choose_layout(template, worst, labels, options, logo, today, worst=True)
    if chosen is None:
        b = ApplicationFormBuilder(
            template,
            worst,
            labels,
            options,
            logo,
            today,
            LAYOUTS[-1],
            measure=True,
            worst=True,
        )
        _ = b.build()
        raise ApplicationDoesNotFitException((b.page1_end or 0) - BOTTOM)
    layout, spread, _room = chosen
    return {
        "name": layout.name,
        "spread": {"with_guardian": spread, "without_guardian": spread},
        ONE_SPREAD: True,
    }
