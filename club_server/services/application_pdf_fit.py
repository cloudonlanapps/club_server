"""Fitting the application form to its pages (#93, applications R16–R18).

The server measures page 1 before drawing it and uses the first layout
whose page 1 fits (``LAYOUTS``); page 1 is then spread to fill the page.
The layout depends on the template alone — every answer is fitted to its
lines — so it is decided when a template is saved, measured for a member
whose every field is too long, and stored. Ported from the prototype.
"""

import io
from datetime import date

from PIL import Image

from ..exceptions import ApplicationDoesNotFitException
from .application_catalogue import JsonDict
from .application_pdf_builder import ApplicationFormBuilder
from .application_pdf_layout import BOTTOM, LAYOUTS, Layout

# Spread leaves this much below page 1's last line.
SPREAD_SLACK = 2


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
# that, and turns on the guardian block.
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
    languages: list[str] = list(options.get("languages") or [])
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


def check(
    template: JsonDict, labels: JsonDict, options: JsonDict, today: date
) -> JsonDict:
    """Decide the template's layout, as on save: the first layout that fits
    with the guardian block, and the spread of page 1 with and without it.

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
    layout, with_guardian, _room = chosen
    b = ApplicationFormBuilder(
        template,
        worst,
        labels,
        options,
        logo,
        today,
        layout,
        measure=True,
        worst=True,
        guardian=False,
    )
    _ = b.build()
    without_guardian = (BOTTOM - (b.page1_end or 0) - SPREAD_SLACK) / max(b.gaps, 1)
    return {
        "name": layout.name,
        "spread": {
            "with_guardian": with_guardian,
            "without_guardian": without_guardian,
        },
    }
