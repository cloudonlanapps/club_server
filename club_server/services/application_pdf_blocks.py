"""The blocks of the application form, in print order (#93, applications R15).

Each takes the builder and draws from its current ``y``. Ported from the
prototype's ``Builder`` methods with their numbers unchanged.
"""

from datetime import date
from typing import TYPE_CHECKING

from .application_catalogue import JsonDict
from .application_pdf_layout import (
    ACCENT,
    ADDRESS_ROWS,
    CHOICE,
    COL_GAP,
    CONTENT_W,
    EXPERIENCE_ROWS,
    FORM_DAY,
    INDENT,
    INK,
    LABEL,
    MARGIN,
    MUTED,
    RULE,
    SEPARATOR,
    TINT,
    VALUE,
    Colour,
)

if TYPE_CHECKING:
    from .application_pdf_builder import ApplicationFormBuilder

PHOTO_W, PHOTO_H = 99.0, 127.0  # 35 x 45 mm
GENDERS = ["male", "female", "other"]


def full_name(m: JsonDict) -> str | None:
    """First, middle and last name."""
    parts = [m.get(k) for k in ("first_name", "middle_name", "last_name")]
    return " ".join(x for x in parts if x) or None


def address(a: JsonDict | None) -> str | None:
    """An address on one line: lines, city, state – pincode."""
    if not a:
        return None
    city = ", ".join(x for x in (a.get("city"), a.get("state")) if x)
    if a.get("pincode"):
        city = f"{city} – {a['pincode']}" if city else a["pincode"]
    parts = [a.get("addr_line1"), a.get("addr_line2"), city]
    return ", ".join(x for x in parts if x) or None


def fmt_date(v: str | None) -> str | None:
    """An ISO date as the form prints it."""
    return f"{date.fromisoformat(v):{FORM_DAY}}" if v else None


def in_columns(
    b: "ApplicationFormBuilder",
    x: float,
    y: float,
    width: float,
    value: dict[str, str] | None,
    size: float,
    bold: bool = False,
    colour: Colour = INK,
) -> float:
    """`value` written once per print language, side by side; return the y below."""
    p = b.pdf
    col = b.columns(width)
    bottom = y
    for i, lang in enumerate(b.langs):
        text = (value or {}).get(lang)
        if text:
            bottom = max(
                bottom,
                p.text_at(x + i * (col + COL_GAP), y, col, text, size, bold, colour),
            )
    return bottom


def column_height(
    b: "ApplicationFormBuilder",
    width: float,
    value: dict[str, str] | None,
    size: float,
    bold: bool = False,
) -> float:
    """The height of the tallest language column of `value`."""
    col = b.columns(width)
    return max(
        (
            b.pdf.text_height((value or {})[lang], col, size, bold)
            for lang in b.langs
            if (value or {}).get(lang)
        ),
        default=0,
    )


def personal(b: "ApplicationFormBuilder", category: int | None):
    """Personal information beside the photo box."""
    b.section("section.personal")
    p = b.pdf
    m = b.m
    photo_x = MARGIN + CONTENT_W - PHOTO_W
    left_w = CONTENT_W - PHOTO_W - COL_GAP
    top = b.y

    p.set_draw_color(*INK)
    p.set_line_width(0.7)
    p.set_dash_pattern(dash=3, gap=2)
    p.rect(photo_x, top, PHOTO_W, PHOTO_H)
    p.set_dash_pattern()
    ptext = "\n".join(b.ml(b.labels["photo"]))
    ph = p.text_height(ptext, PHOTO_W - 12, 7.5)
    _ = p.text_at(
        photo_x + 6,
        top + (PHOTO_H - ph) / 2,
        PHOTO_W - 12,
        ptext,
        7.5,
        colour=MUTED,
        align="C",
    )

    b.y = b.field(MARGIN, b.y, left_w, "field.full_name", full_name(m))
    dob = b.dob()
    b.y = b.fields_row(
        [
            ("field.date_of_birth", f"{dob:{FORM_DAY}}" if dob else None),
            ("field.member_id", m.get("username")),
            ("field.membership_date", fmt_date(m.get("membership_date"))),
        ],
        width=left_w,
    )
    g = m.get("gender")
    b.y = b.choices(
        "field.gender",
        [b.label(f"gender.{k}") for k in GENDERS],
        GENDERS.index(g) if g in GENDERS else None,
        width=left_w,
    )
    cats: list[JsonDict] = b.t.get("age_categories") or []
    if cats:
        b.y = b.choices(
            "field.age_category",
            [SEPARATOR.join(b.ml(c["label"])) for c in cats],
            category,
            width=left_w,
        )
    b.y = max(b.y, top + PHOTO_H + b.gap_base)
    b.y = b.field(
        MARGIN,
        b.y,
        CONTENT_W,
        "field.address",
        address(m.get("address")),
        rows=ADDRESS_ROWS,
    )
    b.y = b.fields_row(
        [("field.phone", m.get("phone")), ("field.email", m.get("email"))]
    )
    b.y = b.fields_row(
        [
            ("field.nationality", m.get("nationality")),
            ("field.preferred_language", m.get("preferred_language")),
        ]
    )


def sports(b: "ApplicationFormBuilder"):
    """Sports details: club/district, position, past experience."""
    b.section("section.sports")
    m = b.m
    b.y = b.field(MARGIN, b.y, CONTENT_W, "field.club_district", m.get("club_district"))
    positions: list[JsonDict] = b.options.get("positions") or []
    if positions:
        values = [pos["value"] for pos in positions]
        chosen = m.get("preferred_position")
        b.y = b.choices(
            "field.preferred_position",
            [SEPARATOR.join(b.ml(pos["label"])) for pos in positions],
            values.index(chosen) if chosen in values else None,
        )
    b.y = b.field(
        MARGIN,
        b.y,
        CONTENT_W,
        "field.past_experience",
        m.get("past_experience"),
        rows=EXPERIENCE_ROWS,
    )


def guardian(b: "ApplicationFormBuilder"):
    """The parent / guardian, in one row."""
    b.section("section.guardian")
    g: JsonDict = b.m.get("guardian") or {}
    b.y = b.fields_row(
        [
            ("field.guardian_name", g.get("name")),
            ("field.guardian_relation", g.get("relation")),
            ("field.guardian_phone", g.get("phone")),
        ]
    )


def selections(b: "ApplicationFormBuilder"):
    """The template's additional selections: each an empty checkbox the member
    ticks by hand, its text in the language columns."""
    items: list[dict[str, str]] = b.t.get("selections") or []
    if not items:
        return
    p = b.pdf
    width = CONTENT_W - INDENT - 14
    need = sum(column_height(b, width, item, CHOICE) + 6 for item in items)
    b.ensure(26 + need)
    b.section("section.selections")
    for item in items:
        p.checkbox(MARGIN + INDENT, b.y + 2, False)
        b.y = in_columns(b, MARGIN + INDENT + 14, b.y, width, item, CHOICE) + b.gap()


def declaration(b: "ApplicationFormBuilder"):
    """The declaration in a box, then the signature lines."""
    p = b.pdf
    decl: dict[str, str] | None = b.t.get("declaration")
    body = column_height(b, CONTENT_W - 20, decl, 8.5)
    # section bar + box + space to sign + the signature labels
    b.ensure(22 + body + 10 + 30 + 20)
    b.section("section.declaration")
    y = b.y
    p.set_draw_color(*RULE)
    p.set_line_width(0.6)
    p.rect(MARGIN, y, CONTENT_W, body + 10)
    _ = in_columns(b, MARGIN + 10, y + 5, CONTENT_W - 20, decl, 8.5)
    y += body + 10 + 30 + b.gap()

    keys = (
        ["sign.date", "sign.guardian", "sign.player"]
        if b.shows_guardian()
        else ["sign.date", "sign.player"]
    )
    col = (CONTENT_W - COL_GAP * (len(keys) - 1)) / len(keys)
    for i, k in enumerate(keys):
        x = MARGIN + i * (col + COL_GAP)
        if k == "sign.date":
            _ = p.text_at(x + 2, y - b.row + 2, col, f"{b.today:{FORM_DAY}}", VALUE)
        p.hline(x, y, col, colour=INK, weight=0.8)
        _ = p.text_at(x, y + 2, col, b.label(k), LABEL, bold=True, colour=MUTED)
    b.y = y + 20


def form_footer(b: "ApplicationFormBuilder"):
    """The template's closing line under the signatures, in a tinted band."""
    text = SEPARATOR.join(b.ml(b.t.get("form_footer")))
    if not text:
        return
    p = b.pdf
    h = p.text_height(text, CONTENT_W - 16, 8.5, bold=True) + 9
    b.ensure(h + 8)
    b.y += b.gap()
    p.set_fill_color(*TINT)
    p.rect(MARGIN, b.y, CONTENT_W, h, style="F")
    _ = p.text_at(
        MARGIN + 8,
        b.y + 4.5,
        CONTENT_W - 16,
        text,
        8.5,
        bold=True,
        colour=ACCENT,
        align="C",
    )
    b.y += h


def terms(b: "ApplicationFormBuilder"):
    """The rules, from page 2 or after the signing part, then the notes."""
    items: list[JsonDict] = b.t.get("terms") or []
    notes: list[JsonDict] = b.t.get("terms_notes") or []
    if not items and not notes:
        return
    p = b.pdf
    if b.layout.split:  # page 2 already holds the signing part: the terms follow it
        b.y += 14
        b.ensure(80)
    else:
        p.add_page()
        b.y = MARGIN
        b.y = (
            in_columns(b, MARGIN, b.y, CONTENT_W, b.t.get("title"), 9, colour=MUTED) + 5
        )
    b.section("section.terms")
    for item in items:
        need = column_height(
            b, CONTENT_W, item.get("heading"), 9, True
        ) + column_height(b, CONTENT_W, item.get("body"), 8.5)
        b.ensure(need + 12)
        b.y = (
            in_columns(
                b,
                MARGIN,
                b.y,
                CONTENT_W,
                item.get("heading"),
                9,
                bold=True,
                colour=ACCENT,
            )
            + 1
        )
        b.y = in_columns(b, MARGIN, b.y, CONTENT_W, item.get("body"), 8.5) + 10
    for n in notes:
        note(b, n)


def note(b: "ApplicationFormBuilder", value: JsonDict):
    """A closing note: its text in each language, one under the other, centred
    in a box, then its attribution."""
    p = b.pdf
    lines = b.ml(value.get("text"))
    attribution = SEPARATOR.join(b.ml(value.get("attribution")))
    width = CONTENT_W - 24
    h = sum(p.text_height(t, width, 9, bold=i == 0) for i, t in enumerate(lines))
    if attribution:
        h += p.text_height(attribution, width, 8, bold=True) + 2
    b.ensure(h + 20)
    top = b.y + 2
    p.set_draw_color(*ACCENT)
    p.set_line_width(0.8)
    p.set_fill_color(*TINT)
    p.rect(MARGIN, top, CONTENT_W, h + 12, style="DF")
    y = top + 6
    for i, t in enumerate(lines):
        y = p.text_at(
            MARGIN + 12,
            y,
            width,
            t,
            9,
            bold=i == 0,
            colour=ACCENT if i else INK,
            align="C",
        )
    if attribution:
        _ = p.text_at(MARGIN + 12, y + 2, width, attribution, 8, bold=True, align="C")
    b.y = top + h + 12 + 8
