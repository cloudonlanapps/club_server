"""The drawing surface of the application form PDF (#93).

An A4 fpdf2 document in points with Lato for Latin text, Noto Sans
Devanagari as its fallback, HarfBuzz shaping, the footer line and, for a
preview, a light watermark. Ported from the prototype.

The same canvas draws a member's values alone over a stored drawing
(#128): it then prints only the footer's left part, so the page number and
the side-margin text come from the drawing beneath.
"""

from datetime import date
from typing import Any

from fpdf import FPDF

from .application_pdf_layout import (
    BOLD,
    CONTENT_W,
    DEVA,
    ELLIPSIS,
    FIT_SIZES,
    FONT_DIR,
    FORM_DAY,
    FONT_FILES,
    INK,
    LEADING,
    MARGIN,
    MUTED,
    PAGE_H,
    PAGE_OF,
    PAGE_W,
    PRINT,
    REGULAR,
    RULE,
    VALUE,
    WATERMARK,
    Colour,
)

SIDE_SIZE = 6
SIDE_ANGLE = 90  # read from the bottom up, in the left margin
WATERMARK_SIZE = 90
WATERMARK_ANGLE = 52
WATERMARK_DROP = 30
FOOTER_SIZE = 7
FOOTER_HEIGHT = 9
FOOTER_RISE = 4
BOX = 8.0  # a checkbox's side
# The average character width, in ems, a long answer's first prefix is
# sized by (``first_lines``); a wrong guess only costs another try.
PROBE_EM = 0.5

# One mark of member data: ("text", page, x, y, width, text, size) or
# ("tick", page, x, y). Recorded so a filled form can be compared with a
# full drawing (#128).
Mark = tuple[object, ...]
JsonDict = dict[str, Any]


class ApplicationPdfCanvas(FPDF):
    """The form's document, its fonts and its drawing primitives."""

    footer_text: str
    watermark: str | None
    side_text: str | None
    page_numbers: bool

    def __init__(
        self,
        footer_text: str,
        watermark: str | None = None,
        side_text: str | None = None,
        page_numbers: bool = True,
        bold: bool = True,
    ):
        """A document whose pages carry ``footer_text`` and, if given,
        ``watermark`` and ``side_text`` (small and grey in the left margin);
        ``page_numbers`` off leaves the footer's right part blank, and
        ``bold`` off loads the regular faces only (a member's values)."""
        super().__init__(unit="pt", format="A4")
        self.footer_text = footer_text
        self.watermark = watermark
        self.side_text = side_text
        self.page_numbers = page_numbers
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(False)
        for family, style, file in FONT_FILES:
            if bold or style != BOLD:
                self.add_font(family, style, str(FONT_DIR / file))
        self.set_fallback_fonts([DEVA], exact_match=False)
        self.set_text_shaping(True)
        self.alias_nb_pages()

    def header(self):
        """The watermark, drawn first so the form prints over it."""
        if self.watermark:
            self.set_font(PRINT, BOLD, WATERMARK_SIZE)
            self.set_text_color(*WATERMARK)
            with self.rotation(WATERMARK_ANGLE, PAGE_W / 2, PAGE_H / 2):
                w = self.get_string_width(self.watermark)
                self.text(
                    PAGE_W / 2 - w / 2, PAGE_H / 2 + WATERMARK_DROP, self.watermark
                )

    def footer(self):
        """The side-margin text, the footer line and the page number."""
        if self.side_text:
            self.font(SIDE_SIZE, colour=MUTED)
            x = MARGIN / 2 + SIDE_SIZE / 3
            y = PAGE_H / 2 + self.get_string_width(self.side_text) / 2
            with self.rotation(SIDE_ANGLE, x, y):
                self.text(x, y, self.side_text)
        self.set_y(PAGE_H - MARGIN - FOOTER_RISE)
        self.font(FOOTER_SIZE, colour=MUTED)
        _ = self.cell(CONTENT_W / 2, FOOTER_HEIGHT, self.footer_text, align="L")
        if not self.page_numbers:
            return
        _ = self.cell(
            CONTENT_W / 2,
            FOOTER_HEIGHT,
            PAGE_OF.format(page=self.page_no()),
            align="R",
        )

    # Primitives -------------------------------------------------------------

    def font(self, size: float, bold: bool = False, colour: Colour = INK):
        """Set the print face at ``size``."""
        self.set_font(PRINT, BOLD if bold else REGULAR, size)
        self.set_text_color(*colour)

    def pin_font(self):
        """Set the current font on the page, outside any text object.

        fpdf2 sets a page's font lazily, inside the first text object after a
        font change; when that text starts in the fallback (Devanagari) face,
        it is set inside the object's ``q … Q`` and lost after it, so the next
        text object has no font. A member's Marathi name, the first value
        written on a filled page, would hit it (#128).
        """
        font = self.current_font
        if font is not None and not self.current_font_is_set_on_page:
            self._out(self._set_font_for_page(font, self.font_size_pt))  # pyright: ignore[reportPrivateUsage]

    def lines(
        self, text: str, width: float, size: float, bold: bool = False
    ) -> list[str]:
        """``text`` broken into the lines it takes across ``width``."""
        self.font(size, bold)
        found: Any = self.multi_cell(
            width, size * LEADING, text, dry_run=True, output="LINES"
        )
        broken: list[str] = found
        return broken

    def text_height(
        self, text: str, width: float, size: float, bold: bool = False
    ) -> float:
        """The height ``text`` takes across ``width``."""
        return len(self.lines(text, width, size, bold)) * size * LEADING

    def text_at(
        self,
        x: float,
        y: float,
        width: float,
        text: str,
        size: float,
        bold: bool = False,
        colour: Colour = INK,
        align: str = "L",
    ) -> float:
        """Write wrapped text from (x, y); return the y below it."""
        self.font(size, bold, colour)
        self.pin_font()
        self.set_xy(x, y)
        _ = self.multi_cell(width, size * LEADING, text, align=align)
        return self.get_y()

    def hline(
        self,
        x: float,
        y: float,
        width: float,
        colour: Colour = RULE,
        weight: float = 0.6,
    ):
        """A solid rule."""
        self.set_draw_color(*colour)
        self.set_line_width(weight)
        self.line(x, y, x + width, y)

    def dotted(self, x: float, y: float, width: float):
        """A line to write on."""
        self.set_draw_color(*RULE)
        self.set_line_width(0.7)
        self.set_dash_pattern(dash=0.8, gap=2.2)
        self.line(x, y, x + width, y)
        self.set_dash_pattern()

    def checkbox(self, x: float, y: float, ticked: bool, size: float = BOX):
        """A box, ticked or empty."""
        self.set_draw_color(*INK)
        self.set_line_width(0.7)
        self.rect(x, y, size, size)
        if ticked:
            self.tick(x, y, size)

    def tick(self, x: float, y: float, size: float = BOX):
        """The tick in the box at (x, y)."""
        self.set_draw_color(*INK)
        self.set_line_width(1.3)
        self.line(x + 1.6, y + size * 0.55, x + size * 0.42, y + size - 1.6)
        self.line(x + size * 0.42, y + size - 1.6, x + size - 1.1, y + 1.1)

    def write_lines(self, place: JsonDict, lines: list[str], size: float) -> list[Mark]:
        """A value's fitted ``lines`` on the lines of its box at ``place``
        (``page``, ``x``, ``y``, ``width``, ``row``)."""
        marks: list[Mark] = []
        for r, line in enumerate(lines):
            base = place["y"] + place["row"] * (r + 1)
            y = base - size * LEADING - 1.5
            _ = self.text_at(place["x"], y, place["width"], line, size)
            marks.append(
                ("text", place["page"], place["x"], y, place["width"], line, size)
            )
        return marks

    def write_date(self, place: JsonDict, today: date) -> Mark:
        """The signature row's date at ``place``."""
        text = f"{today:{FORM_DAY}}"
        _ = self.text_at(place["x"], place["y"], place["width"], text, VALUE)
        return (
            "text",
            place["page"],
            place["x"],
            place["y"],
            place["width"],
            text,
            VALUE,
        )

    def overflows(self, text: str, width: float, size: float, rows: int) -> bool:
        """Whether ``text`` certainly takes more than ``rows`` lines across
        ``width``: its width in one line exceeds ``rows`` + 1 lines'.

        A line holds at most ``width``, and a break drops at most a space,
        so text that fits ``rows`` lines is never wider. Measured only when
        the print face has every character; otherwise it says no.
        """
        self.font(size)
        font = self.current_font
        cmap: dict[int, str] = getattr(font, "cmap", {})
        if not all(ord(c) in cmap for c in text):
            return False
        return self.get_string_width(text) > (rows + 1) * width

    def first_lines(self, text: str, width: float, size: float, rows: int) -> list[str]:
        """``text`` broken across ``width``: exactly its first ``rows`` + 1
        lines, and no more when it takes more.

        Lines are broken greedily, so a prefix of ``text`` that takes
        ``rows`` + 2 lines breaks its first ``rows`` + 1 as the whole text
        does: a long answer is cut from a prefix, not measured in full.
        """
        n = int(width / (size * PROBE_EM) * (rows + 2))
        while n < len(text):
            found = self.lines(text[:n], width, size)
            if len(found) >= rows + 2:
                return found[: rows + 1]
            n *= 2
        return self.lines(text, width, size)

    def fit(self, value: str, width: float, rows: int) -> tuple[list[str], float]:
        """`value` in at most `rows` lines: smaller type if it must, then cut
        with "…". A member's answer never changes the form's shape (R18)."""
        lines: list[str] = []
        size = FIT_SIZES[0]
        for size in FIT_SIZES:
            if size != FIT_SIZES[-1] and self.overflows(value, width, size, rows):
                continue  # certainly too long at this size (#128)
            lines = self.first_lines(value, width, size, rows)
            if len(lines) <= rows:
                return lines, size
        lines = lines[:rows]
        last = lines[-1].rstrip()
        while last and len(self.lines(last + ELLIPSIS, width, size)) > 1:
            last = last[:-1].rstrip()
        lines[-1] = last + ELLIPSIS
        return lines, size
