"""The written answers and the signature after the table (#535, R63).

Ported from the prototype's ``survey_pdf_blocks.dart``.
"""

from .evaluation_pdf_block import EvaluationPdfBlock
from .evaluation_pdf_canvas import EvaluationPdfCanvas
from .evaluation_pdf_layout import (
    ACCENT,
    BLANK_LINE,
    BLANK_LINES,
    BOLD,
    CELL,
    CONTENT_WIDTH,
    GAP,
    HAND,
    HAND_FONT,
    HEADING,
    MUTED,
    PEN,
    SIGNATURE,
    SIGNATURE_LABEL,
    SIGNATURE_SPACE,
    SMALL,
    STAR_GAP,
)


def written_block(heading: str, text: str | None) -> EvaluationPdfBlock:
    """A heading over a handwritten answer, or ruled blanks to fill in."""

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        bottom = c.text_box(
            x,
            y + GAP,
            CONTENT_WIDTH,
            heading,
            size=HEADING,
            colour=ACCENT,
            style=BOLD,
        )
        bottom += 2 * STAR_GAP
        if not text:
            for _ in range(BLANK_LINES):
                bottom += BLANK_LINE
                c.hline(x, x + CONTENT_WIDTH, bottom)
            return bottom
        bottom = c.text_box(
            x, bottom, CONTENT_WIDTH, text, size=HAND, colour=PEN, family=HAND_FONT
        )
        bottom += CELL
        c.hline(x, x + CONTENT_WIDTH, bottom)
        return bottom

    return EvaluationPdfBlock(draw)


def signature_block(signer: str) -> EvaluationPdfBlock:
    """Bottom right: a line, the signer's name in hand, and its label."""

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        left = x + CONTENT_WIDTH - SIGNATURE
        line = y + SIGNATURE_SPACE
        c.hline(left, left + SIGNATURE, line)
        bottom = c.text_box(
            left,
            line + STAR_GAP,
            SIGNATURE,
            signer,
            size=HAND,
            colour=PEN,
            family=HAND_FONT,
            align="C",
        )
        return c.text_box(
            left,
            bottom,
            SIGNATURE,
            SIGNATURE_LABEL,
            size=SMALL,
            colour=MUTED,
            align="C",
        )

    return EvaluationPdfBlock(draw)
