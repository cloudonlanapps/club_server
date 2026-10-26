"""Geometry, type, colours and layouts of the application form PDF (#93).

Ported unchanged from the standalone prototype that settled the form
(`docs/applications_requirements.md`). Lengths are PDF points on A4
(595 × 842); page 1 is spread to fill the page (``application_pdf_fit``).
"""

from pathlib import Path

# Page --------------------------------------------------------------------

MARGIN = 32
PAGE_W, PAGE_H = 595.28, 841.89
CONTENT_W = PAGE_W - 2 * MARGIN
BOTTOM = PAGE_H - MARGIN - 16  # room for the footer
GAP = 4
# Page 1's spare height is shared equally between its gaps, without limit, so
# the page is always full.
COL_GAP = 16
INDENT = 14  # where the writing starts, in from its label

# Type --------------------------------------------------------------------

LABEL = 7.5  # field labels
VALUE = 9.5  # what the member filled in
FIT_SIZES = (VALUE, 8.5, 7.5, 7.0)  # how far an answer shrinks to stay on its lines
CHOICE = 8.5  # checkbox options
ROW = 14.0  # one line to write on (page 1 is then spread to fill)
LEADING = 1.3  # line height as a multiple of the type size

ADDRESS_ROWS = 3
EXPERIENCE_ROWS = 3

# Colours (0..255 channels) -----------------------------------------------

Colour = tuple[int, int, int]

INK: Colour = (20, 24, 31)
MUTED: Colour = (100, 106, 117)
RULE: Colour = (120, 126, 136)
ACCENT: Colour = (24, 63, 120)
TINT: Colour = (236, 241, 248)
WHITE: Colour = (255, 255, 255)
WATERMARK: Colour = (232, 235, 240)

# Fonts (SIL Open Font License, licences beside them) ---------------------

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
PRINT = "Lato"
DEVA = "NotoDeva"
REGULAR = ""
BOLD = "B"
FONT_FILES = (
    (PRINT, REGULAR, "Lato-Regular.ttf"),
    (PRINT, BOLD, "Lato-Bold.ttf"),
    (DEVA, REGULAR, "NotoSansDevanagari-Regular.ttf"),
    (DEVA, BOLD, "NotoSansDevanagari-Bold.ttf"),
)

# Words -------------------------------------------------------------------

ENGLISH = "en"
SEPARATOR = "  |  "
PREVIEW_WATERMARK = "PREVIEW"
PREVIEW_FOOTER = "PREVIEW — not for submission  ·  {day}"
MEMBER_FOOTER = "{username}  ·  downloaded {day}"
PAGE_OF = "Page {page} of {{nb}}"
FOOTER_DAY = "%d %b %Y"
FORM_DAY = "%d / %m / %Y"
ELLIPSIS = "…"


class Layout:
    """One way of fitting the form: line height, gap, and whether the signing
    part opens page 2."""

    name: str
    row: float
    gap: float
    split: bool

    def __init__(self, name: str, row: float, gap: float, split: bool):
        self.name, self.row, self.gap, self.split = name, row, gap, split


# Tried in order; the first whose page 1 fits is used.
LAYOUTS = [
    Layout("one page", ROW, GAP, split=False),
    Layout("one page, tighter spacing", 12.5, 2, split=False),
    Layout("signing part on page 2", ROW, GAP, split=True),
]


def layout_named(name: str) -> Layout | None:
    """The layout called ``name``, if there is one."""
    return next((layout for layout in LAYOUTS if layout.name == name), None)
