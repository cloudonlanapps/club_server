"""The parts of the Question | Rating table (#535, R63).

Ported from the prototype's ``survey_pdf_table.dart`` and
``survey_pdf_answer.dart``. Each part draws its own side, bottom and inner
rules, so a part joins the one above it; the head draws all four.
"""

from collections.abc import Sequence

from .evaluation_pdf_answer_kinds import (
    EvaluationPdfAnswer,
    NotAnswered,
    PieAnswer,
    StarsAnswer,
    TextAnswer,
)
from .evaluation_pdf_canvas import EvaluationPdfCanvas
from .evaluation_pdf_graphics import pie_svg, star_svg
from .evaluation_pdf_layout import (
    ACCENT,
    BAND,
    BODY,
    BOLD,
    CELL,
    CONTENT_WIDTH,
    HAIRLINE,
    HAND,
    HAND_FONT,
    HAND_REMARK,
    HEAD_TRACKING,
    HEADING,
    INK,
    LEADING,
    MUTED,
    NOT_ANSWERED,
    PEN,
    PIE,
    PIE_RING,
    PIE_TEXT,
    QUESTION_HEADER,
    RATING_HEADER,
    RULE,
    SMALL,
    STAR,
    STAR_GAP,
    TABLE_COLUMNS,
    TRACK,
)
from .evaluation_pdf_markdown import PdfTextRun

COLUMN = CONTENT_WIDTH / TABLE_COLUMNS
INNER = COLUMN - 2 * CELL


def _sides(c: EvaluationPdfCanvas, x: float, top: float, bottom: float) -> None:
    """The left, right and bottom rules of a part."""
    c.vline(x, top, bottom)
    c.vline(x + CONTENT_WIDTH, top, bottom)
    c.hline(x, x + CONTENT_WIDTH, bottom)


def draw_head(c: EvaluationPdfCanvas, x: float, y: float) -> float:
    """The shaded head row: QUESTION | RATING."""
    height = SMALL * LEADING + 2 * CELL
    c.fill_box(x, y, CONTENT_WIDTH, height, BAND)
    c.outline_box(x, y, CONTENT_WIDTH, height, colour=RULE, width=HAIRLINE)
    c.vline(x + COLUMN, y, y + height)
    for i, title in enumerate((QUESTION_HEADER, RATING_HEADER)):
        c.text_box(
            x + i * COLUMN + CELL,
            y + CELL,
            INNER,
            title.upper(),
            size=SMALL,
            colour=ACCENT,
            style=BOLD,
            tracking=HEAD_TRACKING,
        )
    return y + height


def bar(title: str):
    """A section's shaded bar across the table."""

    def write(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        return c.text_box(
            x,
            y,
            CONTENT_WIDTH - 2 * CELL,
            title,
            size=HEADING,
            colour=ACCENT,
            style=BOLD,
        )

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        bottom = y + c.measure(write) + 2 * CELL
        c.fill_box(x, y, CONTENT_WIDTH, bottom - y, BAND)
        write(c, x + CELL, y + CELL)
        _sides(c, x, y, bottom)
        return bottom

    return draw


def full_width(runs: Sequence[PdfTextRun]):
    """Info text across the table, small and muted, its links clickable."""

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        bottom = (
            c.runs(
                x + CELL,
                y + CELL,
                CONTENT_WIDTH - 2 * CELL,
                runs,
                size=SMALL,
                colour=MUTED,
            )
            + CELL
        )
        _sides(c, x, y, bottom)
        return bottom

    return draw


def draw_answer(
    c: EvaluationPdfCanvas, x: float, y: float, width: float, shown: EvaluationPdfAnswer
) -> float:
    """Stars, a pie with "value/max", handwritten text, or "Not answered"."""
    match shown:
        case StarsAnswer(count=count, filled=filled):
            for i in range(count):
                star = star_svg(filled=i < filled, colour=ACCENT)
                c.svg(x + i * (STAR + STAR_GAP), y, STAR, star)
            return y + STAR
        case PieAnswer(value=value, max=top):
            ring = pie_svg(
                fraction=value / top, fill=ACCENT, track=TRACK, ring=PIE_RING / PIE
            )
            c.svg(x, y, PIE, ring)
            c.text_box(
                x,
                y + (PIE - SMALL * LEADING) / 2,
                PIE,
                PIE_TEXT.format(value=value, max=top),
                size=SMALL,
                colour=INK,
                style=BOLD,
                align="C",
            )
            return y + PIE
        case TextAnswer(text=text):
            return c.text_box(
                x, y, width, text, size=HAND, colour=PEN, family=HAND_FONT
            )
        case NotAnswered():
            return c.text_box(x, y, width, NOT_ANSWERED, size=SMALL, colour=MUTED)


def question_row(
    question: str,
    evidence: Sequence[PdfTextRun],
    shown: EvaluationPdfAnswer,
    note: str | None,
):
    """One question: its text and evidence | its answer, the note under it."""

    def left(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        bottom = c.text_box(x, y, INNER, question, size=BODY, colour=INK)
        if evidence:
            bottom = c.runs(x, bottom, INNER, evidence, size=SMALL, colour=MUTED)
        return bottom

    def right(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        bottom = draw_answer(c, x, y, INNER, shown)
        if note:
            bottom = c.text_box(
                x,
                bottom + STAR_GAP,
                INNER,
                note,
                size=HAND_REMARK,
                colour=PEN,
                family=HAND_FONT,
            )
        return bottom

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        heights = (c.measure(left), c.measure(right))
        inside = max(heights)
        # Both cells sit in the middle of the row.
        left(c, x + CELL, y + CELL + (inside - heights[0]) / 2)
        right(c, x + COLUMN + CELL, y + CELL + (inside - heights[1]) / 2)
        bottom = y + inside + 2 * CELL
        _sides(c, x, y, bottom)
        c.vline(x + COLUMN, y, bottom)
        return bottom

    return draw
