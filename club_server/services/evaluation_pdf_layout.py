"""Geometry, type, colours and words of the member copy PDF (#535, R63).

Ported from the ``cl_survey_forms`` prototype (``survey_pdf_layout.dart``).
Lengths are PDF points; a tall page is half an A4 sheet cut lengthwise, so
two print side by side on one A4 portrait sheet.
"""

from pathlib import Path

# Points per millimetre.
MM = 72 / 25.4

# Page --------------------------------------------------------------------

PAGE_WIDTH = 105 * MM
PAGE_HEIGHT = 297 * MM
MARGIN = 9 * MM
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
# Height kept for the page number at the foot.
FOOTER = 12
CONTENT_HEIGHT = PAGE_HEIGHT - 2 * MARGIN - FOOTER
# The printed sheet: A4 portrait, two pages side by side.
SHEET = (2 * PAGE_WIDTH, PAGE_HEIGHT)
PAGES_PER_SHEET = 2
# A measuring page: tall enough for any one block.
SCRATCH_HEIGHT = 14_000

# Frame -------------------------------------------------------------------

FRAME_INSET = 4 * MM
FRAME_GAP = 1.6
FRAME_OUTER = 1.2
HAIRLINE = 0.4

# Type --------------------------------------------------------------------

TITLE = 11
HEADING = 7.5
BODY = 6.8
# Handwritten (filled-in) text; the face runs small.
HAND = 8.6
# A handwritten remark: 2 pt under HAND.
HAND_REMARK = HAND - 2
SMALL = 5.8
# Line height as a multiple of the type size.
LEADING = 1.2
# Letter spacing of the table head.
HEAD_TRACKING = 0.4

# Spacing and parts -------------------------------------------------------

CELL = 3
GAP = 6
LOGO = 26
PIE = 20
PIE_RING = 2.6
STAR = 6.5
STAR_GAP = 1
SIGNATURE = 40 * MM
SIGNATURE_SPACE = 22
BLANK_LINES = 2
BLANK_LINE = 11
TABLE_COLUMNS = 2

# Colours (#rrggbb) -------------------------------------------------------

INK = "#1f2933"
MUTED = "#6b7280"
RULE = "#7b8794"
BAND = "#e8edf2"
ACCENT = "#2f4b66"
TRACK = "#d5dbe1"
PEN = "#1d3f8c"
LINK = "#2f5d8a"

# Fonts (SIL Open Font License, licences beside them) ---------------------

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
PRINT_FONT = "Lato"
HAND_FONT = "PatrickHand"
REGULAR = ""
BOLD = "B"
UNDERLINE = "U"
FONT_FILES = (
    (PRINT_FONT, REGULAR, "Lato-Regular.ttf"),
    (PRINT_FONT, BOLD, "Lato-Bold.ttf"),
    (HAND_FONT, REGULAR, "PatrickHand-Regular.ttf"),
)

# Words -------------------------------------------------------------------

NAME_LABEL = "Name"
EVENT_LABEL = "Event"
REVIEW_DATE_LABEL = "Review Date"
PERIOD_LABEL = "Review period"
PERIOD_SEPARATOR = "to"
QUESTION_HEADER = "Question"
RATING_HEADER = "Rating"
SIGNATURE_LABEL = "Signature"
NOT_ANSWERED = "Not answered"
EVIDENCE_LABEL = "Evidence"
YES = "Yes"
NO = "No"
LIST_SEPARATOR = ", "
LABEL_SUFFIX = ":  "
PAGE_OF = "{page} / {pages}"
PIE_TEXT = "{value}/{max}"
RANGE_TEXT = "{value} / {max}"
DATE_FORMAT = "{day} {month} {year}"
MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MS_PER_SECOND = 1000

# Media -------------------------------------------------------------------

# The site_media purpose the club's public site shows as its logo.
LOGO_PURPOSE = "logo"
LOGO_MEDIA_TYPE = "image"
EVIDENCE_PATH = "/media/by_id/{uuid}/download"
