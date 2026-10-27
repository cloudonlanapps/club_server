"""The application form, laid out and drawn (#93, applications R15–R18).

Ported from the prototype with its numbers unchanged. The form's blocks
(personal details, sports, guardian, selections, declaration, footer line,
terms) are in ``application_pdf_blocks``; this module holds the builder's
state, its flow and the field primitives they share.

A form prints the template's ``languages``: English, or English and one
other language. The member's preferred language plays no part. Page 1 is
spread to fill the page: the form is laid out once to measure, then again
with the spare height shared out between its gaps (``application_pdf_fit``).
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
from .application_pdf_canvas import ApplicationPdfCanvas
from .application_pdf_layout import (
    ACCENT,
    BOTTOM,
    CHOICE,
    COL_GAP,
    CONTENT_W,
    ELLIPSIS,
    ENGLISH,
    FIT_SIZES,
    FOOTER_DAY,
    INDENT,
    LABEL,
    LAYOUTS,
    LEADING,
    MARGIN,
    MEMBER_FOOTER,
    MUTED,
    PREVIEW_FOOTER,
    PREVIEW_WATERMARK,
    RULE,
    SEPARATOR,
    TINT,
    VALUE,
    WHITE,
    Layout,
)
from .application_rules import choose_category, is_minor

MAX_LANGUAGES = 2
LOGO = 58.0
SECTION_NEED = 60


class ApplicationFormBuilder:
    """One drawing (or measuring) of a member's form."""

    guardian_forced: bool | None
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
        guardian: bool | None = None,
        preview: bool = False,
    ):
        """A builder for ``member``'s form of the resolved ``template``."""
        self.guardian_forced = guardian  # None: decided by the member
        self.layout = layout or LAYOUTS[0]
        self.row = self.layout.row
        self.gap_base = self.layout.gap
        self.measure = (
            measure  # laying out only to measure page 1: no page breaks, no terms
        )
        self.worst = worst  # the worst case: no eligibility test, guardian block shown
        self.t = template
        self.m = member
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
        day = f"{today:{FOOTER_DAY}}"
        if preview:
            self.pdf = ApplicationPdfCanvas(
                PREVIEW_FOOTER.format(day=day), watermark=PREVIEW_WATERMARK
            )
        else:
            self.pdf = ApplicationPdfCanvas(
                MEMBER_FOOTER.format(username=member["username"], day=day)
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
        self.pdf.add_page()
        self.header()
        blocks.personal(self, category)
        blocks.sports(self)
        if self.shows_guardian():
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
        return self.pdf

    # Eligibility ------------------------------------------------------------

    def age_category(self) -> int | None:
        """The age category ticked: the one the member fits, or the one they
        chose among several (R11, R11a)."""
        cats: list[JsonDict] = self.t.get("age_categories") or []
        chosen: int | None = self.m.get("chosen_category")
        if self.worst:
            return None
        if not cats:
            if chosen is not None:  # a template without categories has none to choose
                raise ApplicationCategoryNotFittingException(chosen, [])
            return None
        return choose_category(cats, self.dob(), self.m.get("gender"), chosen)

    def dob(self) -> date | None:
        """The member's date of birth."""
        v = self.m.get("date_of_birth")
        return date.fromisoformat(v) if v else None

    def shows_guardian(self) -> bool:
        """Whether the guardian block prints: a minor, or a guardian set."""
        if self.guardian_forced is not None:
            return self.guardian_forced
        return (
            self.worst
            or is_minor(self.dob(), self.today)
            or bool(self.m.get("guardian"))
        )

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
        value: str | None,
        rows: int = 1,
    ) -> float:
        """A label, then `rows` dotted lines set in from it, the value written on
        the first ones; return the y below."""
        p = self.pdf
        y = self.field_label(x, y, width, key)
        lx, lw = x + INDENT, width - INDENT
        lines, size = self.fit(value, lw - 4, rows) if value else ([], VALUE)
        for r in range(rows):
            base = y + self.row * (r + 1)
            if r < len(lines):
                _ = p.text_at(
                    lx + 2, base - size * LEADING - 1.5, lw - 4, lines[r], size
                )
            p.dotted(lx, base, lw)
        return y + self.row * rows + self.gap()

    def fit(self, value: str, width: float, rows: int) -> tuple[list[str], float]:
        """`value` in at most `rows` lines: smaller type if it must, then cut
        with "…". A member's answer never changes the form's shape."""
        p = self.pdf
        lines: list[str] = []
        size = VALUE
        for size in FIT_SIZES:
            lines = p.lines(value, width, size)
            if len(lines) <= rows:
                return lines, size
        lines = lines[:rows]
        last = lines[-1].rstrip()
        while last and len(p.lines(last + ELLIPSIS, width, size)) > 1:
            last = last[:-1].rstrip()
        lines[-1] = last + ELLIPSIS
        return lines, size

    def fields_row(
        self,
        specs: list[tuple[str, str | None]],
        x: float = MARGIN,
        width: float = CONTENT_W,
    ) -> float:
        """Fields side by side in equal columns; return the y below the tallest."""
        col = (width - COL_GAP * (len(specs) - 1)) / len(specs)
        gaps = self.gaps
        bottom = max(
            self.field(x + i * (col + COL_GAP), self.y, col, k, v)
            for i, (k, v) in enumerate(specs)
        )
        self.gaps = gaps + (1 if self.pdf.page_no() == 1 else 0)  # one row, one gap
        return bottom

    def choices(
        self,
        key: str,
        options: list[str],
        ticked: int | None,
        x: float = MARGIN,
        width: float = CONTENT_W,
        columns: int = 3,
    ) -> float:
        """A label and a checkbox per option, the member's ticked."""
        p = self.pdf
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
            p.checkbox(cx, row_top + 2, i == ticked)
            row_bottom = max(
                row_bottom, p.text_at(cx + 12, row_top, col - 15, text, CHOICE)
            )
        return row_bottom + self.gap()
