"""The drawing surface of the member copy PDF (#535, R63)."""

import io
from collections.abc import Callable, Sequence

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .evaluation_pdf_layout import (
    FONT_DIR,
    FONT_FILES,
    HAIRLINE,
    LEADING,
    LINK,
    PAGE_WIDTH,
    PRINT_FONT,
    REGULAR,
    RULE,
    SCRATCH_HEIGHT,
    SHEET,
    UNDERLINE,
)
from .evaluation_pdf_markdown import PdfTextRun

# A drawing at (x, y) that returns the y it ended at.
Draw = Callable[["EvaluationPdfCanvas", float, float], float]

HEX_BASE = 16
HEX_CHANNELS = (slice(1, 3), slice(3, 5), slice(5, 7))


def rgb(colour: str) -> tuple[int, int, int]:
    """A ``#rrggbb`` colour as 0..255 channels."""
    red, green, blue = (int(colour[part], HEX_BASE) for part in HEX_CHANNELS)
    return red, green, blue


class EvaluationPdfCanvas(FPDF):
    """An fpdf2 document in points with the PDF's fonts and a few primitives.

    Every primitive takes a top-left position and returns the y it ended at,
    so a block is a function that draws from a point and reports its bottom.
    A block's height is measured by drawing it on a scratch canvas, which is
    discarded: the same code measures and draws, so the two never disagree.
    Compression is off so the content stays inspectable.
    """

    def __init__(self, page_size: tuple[float, float] = SHEET):
        """A canvas whose pages are ``page_size`` points."""
        super().__init__(orientation="P", unit="pt", format=page_size)
        self.set_compression(False)
        self.set_auto_page_break(False)
        self.set_margins(0, 0, 0)
        self.c_margin = 0
        for family, style, file in FONT_FILES:
            self.add_font(family, style, str(FONT_DIR / file))
        self._scratch: EvaluationPdfCanvas | None = None

    # Measuring ---------------------------------------------------------

    def measure(self, draw: Draw) -> float:
        """The height ``draw`` takes, drawn on a scratch canvas."""
        if self._scratch is None:
            scratch = EvaluationPdfCanvas((PAGE_WIDTH, SCRATCH_HEIGHT))
            scratch.add_page()
            # A scratch canvas measures on itself: only heights matter there.
            scratch._scratch = scratch
            self._scratch = scratch
        return draw(self._scratch, 0, 0)

    def width_of(self, text: str, *, family: str, style: str, size: float) -> float:
        """The width of one line of ``text``."""
        self.set_font(family, style, size)
        return self.get_string_width(text)

    # Text --------------------------------------------------------------

    def text_box(
        self,
        x: float,
        y: float,
        width: float,
        text: str,
        *,
        size: float,
        colour: str,
        family: str = PRINT_FONT,
        style: str = REGULAR,
        align: str = "L",
        tracking: float = 0,
    ) -> float:
        """``text`` wrapped to ``width``; returns its bottom."""
        self.set_font(family, style, size)
        self.set_char_spacing(tracking)
        self.set_text_color(*rgb(colour))
        self.set_xy(x, y)
        self.multi_cell(
            width,
            size * LEADING,
            text,
            align=align,
            new_x=XPos.LEFT,
            new_y=YPos.NEXT,
        )
        self.set_char_spacing(0)
        return self.get_y()

    def runs(
        self,
        x: float,
        y: float,
        width: float,
        runs: Sequence[PdfTextRun],
        *,
        size: float,
        colour: str,
        link_colour: str = LINK,
    ) -> float:
        """Flowing text whose linked runs are underlined and clickable."""
        line = size * LEADING
        left, right = self.l_margin, self.r_margin
        self.set_left_margin(x)
        self.set_right_margin(self.w - x - width)
        self.set_xy(x, y)
        for run in runs:
            self.set_font(PRINT_FONT, UNDERLINE if run.link else REGULAR, size)
            self.set_text_color(*rgb(link_colour if run.link else colour))
            self.write(line, run.text, link=run.link or "")
        bottom = self.get_y() + line
        self.set_left_margin(left)
        self.set_right_margin(right)
        return bottom

    # Lines, fills and pictures ----------------------------------------

    def hline(
        self, x1: float, x2: float, y: float, *, colour: str = RULE, width=HAIRLINE
    ) -> None:
        """A horizontal rule."""
        self.set_draw_color(*rgb(colour))
        self.set_line_width(width)
        self.line(x1, y, x2, y)

    def vline(
        self, x: float, y1: float, y2: float, *, colour: str = RULE, width=HAIRLINE
    ) -> None:
        """A vertical rule."""
        self.set_draw_color(*rgb(colour))
        self.set_line_width(width)
        self.line(x, y1, x, y2)

    def fill_box(self, x: float, y: float, w: float, h: float, colour: str) -> None:
        """A filled rectangle without an outline."""
        self.set_fill_color(*rgb(colour))
        self.rect(x, y, w, h, style="F")

    def outline_box(
        self, x: float, y: float, w: float, h: float, *, colour: str, width: float
    ) -> None:
        """A rectangle's outline."""
        self.set_draw_color(*rgb(colour))
        self.set_line_width(width)
        self.rect(x, y, w, h, style="D")

    def svg(self, x: float, y: float, size: float, source: str) -> None:
        """An SVG graphic scaled into a ``size`` square."""
        self.image(io.BytesIO(source.encode()), x=x, y=y, w=size, h=size)

    def picture(self, x: float, y: float, size: float, data: bytes) -> None:
        """A raster picture fitted, aspect kept, into a ``size`` square."""
        self.image(io.BytesIO(data), x=x, y=y, w=size, h=size, keep_aspect_ratio=True)
