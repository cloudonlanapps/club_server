"""The application form, laid out and drawn (#93, applications R15–R18).

Ported from the prototype with its numbers unchanged. The form's blocks
(personal details, sports, guardian, selections, declaration, footer line,
terms) are in ``application_pdf_blocks``; this module holds the builder's
state, its flow and the field primitives they share.

A form prints the template's ``languages``: English, or English and one
other language. The member's preferred language plays no part. Page 1 is
spread to fill the page: the form is laid out once to measure, then again
with the spare height shared out between its gaps (``application_pdf_fit``).

A builder records where each member value and tick goes (``places``). A
blank builder draws the form with no member in it: that drawing and its
places are stored on publish, and a download writes only the member's
values over it (``application_pdf_fill``, #128). Both write a value with
the canvas's ``write_lines`` and ``tick``, so a filled form is
the full drawing's twin.
"""

import io
from datetime import date

from fpdf import FPDF

from ..exceptions import (
    ApplicationCategoryNotFittingException,
    ApplicationReferenceException,
)
from . import application_pdf_blocks as blocks
from .application_catalogue import JsonDict
from .application_pdf_canvas import ApplicationPdfCanvas, Mark
from .application_pdf_layout import (
    ACCENT,
    BOTTOM,
    CHOICE,
    COL_GAP,
    CONTENT_W,
    ENGLISH,
    FOOTER_DAY,
    INDENT,
    LABEL,
    LAYOUTS,
    MARGIN,
    MEMBER_FOOTER,
    MUTED,
    PREVIEW_FOOTER,
    PREVIEW_WATERMARK,
    PUBLISH_MARK,
    RULE,
    SEPARATOR,
    TINT,
    WHITE,
    Layout,
)
from .application_rules import choose_category

MAX_LANGUAGES = 2
LOGO = 58.0
SECTION_NEED = 60
# Bumped when what ``places`` records, or how a value is written from it,
# changes: a stored drawing of another version is drawn again (#128).
PLACES_VERSION = 1


def form_category(template: JsonDict, member: JsonDict) -> int | None:
    """The age category ticked: the one the member fits, or the one they
    chose among several (R11, R11a)."""
    cats: list[JsonDict] = template.get("age_categories") or []
    chosen: int | None = member.get("chosen_category")
    if not cats:
        if chosen is not None:  # a template without categories has none to choose
            raise ApplicationCategoryNotFittingException(chosen, [])
        return None
    v = member.get("date_of_birth")
    return choose_category(
        cats, date.fromisoformat(v) if v else None, member.get("gender"), chosen
    )


class ApplicationFormBuilder:
    """One drawing (or measuring) of a member's form."""

    layout: Layout
    row: float
    gap_base: float
    measure: bool
    worst: bool
    t: JsonDict
    m: JsonDict
    options: JsonDict
    labels: JsonDict
    langs: list[str]
    logo: bytes | None
    today: date
    extra: float
    gaps: int
    page1_end: float | None
    pdf: ApplicationPdfCanvas
    y: float
    blank: bool
    values: dict[str, str | None]
    ticks: dict[str, set[int]]
    places: JsonDict
    marks: list[Mark]

    def __init__(
        self,
        template: JsonDict,
        member: JsonDict,
        labels: JsonDict,
        options: JsonDict,
        logo: bytes | None,
        today: date,
        layout: Layout | None = None,
        extra: float = 0,
        measure: bool = False,
        worst: bool = False,
        preview: bool = False,
        publish_id: str | None = None,
        blank: bool = False,
    ):
        """A builder for ``member``'s form of the printable ``template``;
        ``publish_id`` is printed in the side margin of every page. A
        ``blank`` form has no member in it: no values, no ticks, no
        signature date and no footer text, only the places they go."""
        self.blank = blank
        self.layout = layout or LAYOUTS[0]
        self.row = self.layout.row
        self.gap_base = self.layout.gap
        self.measure = (
            measure  # laying out only to measure page 1: no page breaks, no terms
        )
        self.worst = worst  # the worst case: no eligibility test
        self.t = template
        self.m = {} if blank else member
        self.options = options
        self.labels = {**labels, **(template.get("label_overrides") or {})}
        self.langs = self.print_languages()
        self.logo = logo
        self.today = today
        self.extra = (
            extra  # spare height added to each gap on page 1 (page 1 is known to fit)
        )
        self.gaps = 0  # how many gaps page 1 has
        self.page1_end = None  # where page 1's content ends, if it fits
        self.values = blocks.field_values(self.m)
        self.ticks = {}
        self.places = {
            "version": PLACES_VERSION,
            "pages": 0,
            "fields": {},
            "choices": {},
            "sign_date": None,
        }
        self.marks = []
        day = f"{today:{FOOTER_DAY}}"
        if preview:
            self.pdf = ApplicationPdfCanvas(
                PREVIEW_FOOTER.format(day=day), watermark=PREVIEW_WATERMARK
            )
        else:
            self.pdf = ApplicationPdfCanvas(
                ""
                if blank
                else MEMBER_FOOTER.format(username=member["username"], day=day),
                side_text=PUBLISH_MARK.format(publish_id=publish_id)
                if publish_id
                else None,
            )
        self.y = MARGIN

    # Languages --------------------------------------------------------------

    def print_languages(self) -> list[str]:
        """English, then the one other language the admin chose, if any."""
        langs: list[str] = self.t["languages"]
        if not langs or langs[0] != ENGLISH or len(langs) > MAX_LANGUAGES:
            raise ApplicationReferenceException(
                'languages must be ["en"] or ["en", <one other>]'
            )
        missing = [
            k for k, v in self.labels.items() if not all(v.get(lang) for lang in langs)
        ]
        if missing:
            raise ApplicationReferenceException(
                f"no {langs[-1]!r} text for the labels {', '.join(missing[:3])}"
            )
        return langs

    def ml(self, value: dict[str, str] | None) -> list[str]:
        """The value's text in each print language that has one, in order."""
        if not value:
            return []
        out: list[str] = []
        for lang in self.langs:
            text = value.get(lang)
            if text and text not in out:
                out.append(text)
        return out

    def text(self, key: str) -> dict[str, str]:
        """One of the template's resolved texts, or none."""
        value: dict[str, str] = self.t.get(key) or {}
        return value

    def label(self, key: str) -> str:
        """A standard label, "English | other"."""
        return SEPARATOR.join(self.ml(self.labels[key]))

    def columns(self, width: float) -> float:
        """The width of one language column across `width`."""
        n = len(self.langs)
        return (width - COL_GAP * (n - 1)) / n

    # Flow -------------------------------------------------------------------

    def gap(self) -> float:
        """The gap below a row; page 1's carry its share of the spare height."""
        if self.pdf.page_no() == 1:
            self.gaps += 1
            return self.gap_base + self.extra
        return self.gap_base

    def ensure(self, height: float):
        """Open a page when ``height`` would not fit; page 1 never breaks."""
        # Page 1 was measured and fits: it never breaks. Measuring never breaks.
        if self.measure or self.pdf.page_no() == 1:
            return
        if self.y + height > BOTTOM:
            self.pdf.add_page()
            self.y = MARGIN

    def build(self) -> FPDF:
        """Lay the form out and draw it."""
        category = self.age_category()
        self.ticks = blocks.ticks(self.m, self.options, category)
        self.pdf.add_page()
        self.header()
        blocks.personal(self)
        blocks.sports(self)
        blocks.guardian(self)
        if self.layout.split:  # the signing part opens page 2
            self.page1_end = self.y
            self.pdf.add_page()
            self.y = MARGIN
        blocks.selections(self)
        blocks.declaration(self)
        blocks.form_footer(self)
        if not self.layout.split:
            self.page1_end = self.y
        if not self.measure:
            blocks.terms(self)
        self.places["pages"] = self.pdf.page_no()
        return self.pdf

    # Eligibility ------------------------------------------------------------

    def age_category(self) -> int | None:
        """The age category ticked (R11, R11a); none for the worst case or a
        blank form."""
        if self.worst or self.blank:
            return None
        return form_category(self.t, self.m)

    # Shared blocks ----------------------------------------------------------

    def header(self):
        """The logo, then one column per language: organisation, title, subtitle."""
        p = self.pdf
        logo = LOGO if self.logo else 0.0
        x0 = MARGIN + (logo + 14 if logo else 0)
        col = self.columns(CONTENT_W - (x0 - MARGIN))
        top = self.y
        bottom = top
        for i, lang in enumerate(self.langs):
            x = x0 + i * (col + COL_GAP)
            y = top
            org = self.text("organisation").get(lang)
            if org:
                y = p.text_at(x, y, col, org, 11.5, bold=True, colour=ACCENT)
            title = self.text("title").get(lang)
            if title:
                y = p.text_at(x, y + 2, col, title, 9.5, bold=True)
            subtitle = self.text("subtitle").get(lang)
            if subtitle:
                y = p.text_at(x, y + 1, col, subtitle, 7.5, colour=MUTED)
            bottom = max(bottom, y)
            if i:  # a thin rule between the language columns
                p.set_draw_color(*RULE)
                p.set_line_width(0.5)
                p.line(x - COL_GAP / 2, top + 2, x - COL_GAP / 2, y - 2)
        if self.logo:
            _ = p.image(io.BytesIO(self.logo), MARGIN, top, logo, logo)
        y = max(bottom, top + logo) + 5

        # each language on its own line within a cell
        header: list[dict[str, str]] = self.t.get("header") or []
        items = ["\n".join(self.ml(h)) for h in header]
        if items:
            # one cell per item, each wrapped and centred in its own width
            cell = CONTENT_W / len(items)
            h = max(p.text_height(t, cell - 12, 8) for t in items) + 7
            p.set_fill_color(*TINT)
            p.rect(MARGIN, y, CONTENT_W, h, style="F")
            for i, t in enumerate(items):
                cx = MARGIN + i * cell
                th = p.text_height(t, cell - 12, 8)
                _ = p.text_at(cx + 6, y + (h - th) / 2, cell - 12, t, 8, align="C")
                if i:
                    p.set_draw_color(*WHITE)
                    p.set_line_width(1.2)
                    p.line(cx, y, cx, y + h)
            y += h
        p.hline(MARGIN, y + 3, CONTENT_W, colour=ACCENT, weight=1.1)
        self.y = y + 8

    def section(self, key: str):
        """A section bar with its label."""
        self.ensure(SECTION_NEED)
        p = self.pdf
        text = self.label(key)
        h = p.text_height(text, CONTENT_W - 16, 8.5, bold=True) + 5
        p.set_fill_color(*ACCENT)
        p.rect(MARGIN, self.y, CONTENT_W, h, style="F")
        _ = p.text_at(
            MARGIN + 8, self.y + 2.5, CONTENT_W - 16, text, 8.5, bold=True, colour=WHITE
        )
        self.y += h + self.gap()

    def field_label(self, x: float, y: float, width: float, key: str) -> float:
        """A field's label; return the y below it."""
        return self.pdf.text_at(
            x, y, width, self.label(key), LABEL, bold=True, colour=MUTED
        )

    def field(
        self,
        x: float,
        y: float,
        width: float,
        key: str,
        rows: int = 1,
    ) -> float:
        """A label, then `rows` dotted lines set in from it, the member's value
        for ``key`` written on the first ones; return the y below."""
        p = self.pdf
        y = self.field_label(x, y, width, key)
        lx, lw = x + INDENT, width - INDENT
        for r in range(rows):
            p.dotted(lx, y + self.row * (r + 1), lw)
        place: JsonDict = {
            "page": p.page_no(),
            "x": lx + 2,
            "y": y,
            "width": lw - 4,
            "rows": rows,
            "row": self.row,
        }
        self.places["fields"][key] = place
        value = self.values.get(key)
        if value:
            lines, size = p.fit(value, place["width"], rows)
            self.marks += p.write_lines(place, lines, size)
        return y + self.row * rows + self.gap()

    def fit(self, value: str, width: float, rows: int) -> tuple[list[str], float]:
        """`value` fitted to `rows` lines (R18): ``ApplicationPdfCanvas.fit``."""
        return self.pdf.fit(value, width, rows)

    def fields_row(
        self,
        specs: list[str],
        x: float = MARGIN,
        width: float = CONTENT_W,
    ) -> float:
        """Fields side by side in equal columns; return the y below the tallest."""
        col = (width - COL_GAP * (len(specs) - 1)) / len(specs)
        gaps = self.gaps
        bottom = max(
            self.field(x + i * (col + COL_GAP), self.y, col, k)
            for i, k in enumerate(specs)
        )
        self.gaps = gaps + (1 if self.pdf.page_no() == 1 else 0)  # one row, one gap
        return bottom

    def choices(
        self,
        key: str,
        options: list[str],
        x: float = MARGIN,
        width: float = CONTENT_W,
        columns: int = 3,
    ) -> float:
        """A label and a checkbox per option, the member's ticked (one or several)."""
        p = self.pdf
        chosen = self.ticks.get(key) or set()
        boxes: list[JsonDict] = []
        self.places["choices"][key] = boxes
        y = self.field_label(x, self.y, width, key) + 3
        lx = x + INDENT
        # fewer columns when an option would not fit on one line
        while columns > 1:
            col = (width - INDENT) / columns
            if all(len(p.lines(t, col - 15, CHOICE)) == 1 for t in options):
                break
            columns -= 1
        col = (width - INDENT) / columns
        row_top = row_bottom = y
        for i, text in enumerate(options):
            if i and i % columns == 0:
                row_top = row_bottom + 2
            cx = lx + (i % columns) * col
            box: JsonDict = {"page": p.page_no(), "x": cx, "y": row_top + 2}
            boxes.append(box)
            p.checkbox(cx, box["y"], i in chosen)
            if i in chosen:
                self.marks.append(("tick", box["page"], cx, box["y"]))
            row_bottom = max(
                row_bottom, p.text_at(cx + 12, row_top, col - 15, text, CHOICE)
            )
        return row_bottom + self.gap()
