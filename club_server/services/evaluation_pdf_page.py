"""Pages and sheets of the member copy PDF (#535, R63).

Ported from the prototype's ``survey_pdf_page.dart`` and the sheet loop of
``survey_pdf.dart``: each tall page has a double frame, its blocks and its
number; two pages print side by side on one A4 sheet, page 1 on the left,
and a lone last page leaves the right half blank.
"""

from .evaluation_pdf_block import EvaluationPdfBlock
from .evaluation_pdf_canvas import EvaluationPdfCanvas
from .evaluation_pdf_layout import (
    ACCENT,
    CONTENT_WIDTH,
    FRAME_GAP,
    FRAME_INSET,
    FRAME_OUTER,
    HAIRLINE,
    LEADING,
    MARGIN,
    MUTED,
    PAGE_HEIGHT,
    PAGE_OF,
    PAGE_WIDTH,
    PAGES_PER_SHEET,
    SMALL,
)


def _frame(c: EvaluationPdfCanvas, left: float) -> None:
    """The double frame: a heavy outer line, a gap, a hairline inside it."""
    for inset, width in (
        (FRAME_INSET, FRAME_OUTER),
        (FRAME_INSET + FRAME_GAP, HAIRLINE),
    ):
        # A stroke is centred on its path; keep it inside the inset.
        edge = inset + width / 2
        c.outline_box(
            left + edge,
            edge,
            PAGE_WIDTH - 2 * edge,
            PAGE_HEIGHT - 2 * edge,
            colour=ACCENT,
            width=width,
        )


def _page(
    c: EvaluationPdfCanvas,
    left: float,
    blocks: list[EvaluationPdfBlock],
    indexes: list[int],
    number: str,
) -> None:
    """One tall page with its left edge at ``left``."""
    _frame(c, left)
    x = left + MARGIN
    y = MARGIN
    for n, index in enumerate(indexes):
        block = blocks[index]
        top = y
        y = block.draw(c, x, y)
        if n == 0 and block.table_part:
            # The top line the part above, on the previous page, drew;
            # drawn last so a shaded bar does not cover it.
            c.hline(x, x + CONTENT_WIDTH, top)
    c.text_box(
        x,
        PAGE_HEIGHT - MARGIN - SMALL * LEADING,
        CONTENT_WIDTH,
        number,
        size=SMALL,
        colour=MUTED,
        align="R",
    )


def draw_sheets(
    c: EvaluationPdfCanvas, blocks: list[EvaluationPdfBlock], pages: list[list[int]]
) -> None:
    """Every page, two to a sheet."""
    for i, indexes in enumerate(pages):
        if i % PAGES_PER_SHEET == 0:
            c.add_page()
        number = PAGE_OF.format(page=i + 1, pages=len(pages))
        _page(c, (i % PAGES_PER_SHEET) * PAGE_WIDTH, blocks, indexes, number)
