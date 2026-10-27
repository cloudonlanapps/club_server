"""The drawing surface of the application form PDF (#93).

An A4 fpdf2 document in points with Lato for Latin text, Noto Sans
Devanagari as its fallback, HarfBuzz shaping, the footer line and, for a
preview, a light watermark. Ported from the prototype.
"""

from typing import Any

from fpdf import FPDF

from .application_pdf_layout import (
    BOLD,
    CONTENT_W,
    DEVA,
    FONT_DIR,
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


class ApplicationPdfCanvas(FPDF):
    """The form's document, its fonts and its drawing primitives."""

    footer_text: str
    watermark: str | None
    side_text: str | None

    def __init__(
        self,
        footer_text: str,
        watermark: str | None = None,
        side_text: str | None = None,
    ):
        """A document whose pages carry ``footer_text`` and, if given,
        ``watermark`` and ``side_text`` (small and grey in the left margin)."""
        super().__init__(unit="pt", format="A4")
        self.footer_text = footer_text
        self.watermark = watermark
        self.side_text = side_text
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(False)
        for family, style, file in FONT_FILES:
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

    def checkbox(self, x: float, y: float, ticked: bool, size: float = 8.0):
        """A box, ticked or empty."""
        self.set_draw_color(*INK)
        self.set_line_width(0.7)
        self.rect(x, y, size, size)
        if ticked:
            self.set_line_width(1.3)
            self.line(x + 1.6, y + size * 0.55, x + size * 0.42, y + size - 1.6)
            self.line(x + size * 0.42, y + size - 1.6, x + size - 1.1, y + 1.1)
