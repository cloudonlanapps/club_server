"""The head of the first page (#535, R63).

Ported from the prototype's ``survey_pdf_header.dart``: the title centred
with the club logo on the right, then the member's name and the review
period, each value handwritten on a ruled blank. An event review also
names its event, and a published one its review date (the publication date).
"""

from .evaluation_pdf_block import EvaluationPdfBlock
from .evaluation_pdf_canvas import EvaluationPdfCanvas
from .evaluation_pdf_layout import (
    ACCENT,
    BODY,
    BOLD,
    CELL,
    CONTENT_WIDTH,
    EVENT_LABEL,
    GAP,
    HAND,
    HAND_FONT,
    INK,
    LABEL_SUFFIX,
    LEADING,
    LOGO,
    MUTED,
    NAME_LABEL,
    PEN,
    PERIOD_LABEL,
    PERIOD_SEPARATOR,
    PRINT_FONT,
    REGULAR,
    REVIEW_DATE_LABEL,
    SMALL,
    TITLE,
)


def _title(title: str, logo: bytes | None) -> EvaluationPdfBlock:
    # The logo's width is kept free on the left too, so the title stays centred.
    side = LOGO if logo else 0
    width = CONTENT_WIDTH - 2 * side

    def write(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        return c.text_box(
            x, y, width, title, size=TITLE, colour=ACCENT, style=BOLD, align="C"
        )

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        height = c.measure(write)
        row = max(height, side)
        write(c, x + side, y + (row - height) / 2)
        if logo:
            c.picture(x + CONTENT_WIDTH - side, y + (row - side) / 2, side, logo)
        return y + row + GAP

    return EvaluationPdfBlock(draw, keep_with_next=True)


def _field(label: str, parts: list[str], after: float) -> EvaluationPdfBlock:
    """ "label: value …", each even part a value on its own ruled blank (an
    empty one left to fill in by hand), each odd part a connecting word."""
    label_text = f"{label}{LABEL_SUFFIX}"

    def draw(c: EvaluationPdfCanvas, x: float, y: float) -> float:
        label_width = c.width_of(label_text, family=PRINT_FONT, style=BOLD, size=BODY)
        joins = [
            c.width_of(p, family=PRINT_FONT, style=REGULAR, size=SMALL) + 2 * CELL
            for p in parts[1::2]
        ]
        values = parts[0::2]
        slot = (CONTENT_WIDTH - label_width - sum(joins)) / len(values)

        def value(text: str):
            return lambda s, vx, vy: s.text_box(
                vx, vy, slot, text, size=HAND, colour=PEN, family=HAND_FONT
            )

        heights = [c.measure(value(v)) for v in values]
        row = max([BODY * LEADING, SMALL * LEADING, *heights])
        bottom = y + row
        c.text_box(
            x,
            bottom - BODY * LEADING,
            label_width,
            label_text,
            size=BODY,
            colour=INK,
            style=BOLD,
        )
        left = x + label_width
        for i, part in enumerate(parts):
            if i % 2:
                width = joins[i // 2]
                c.text_box(
                    left + CELL,
                    bottom - SMALL * LEADING,
                    width,
                    part,
                    size=SMALL,
                    colour=MUTED,
                )
            else:
                width = slot
                value(part)(c, left, bottom - heights[i // 2])
                c.hline(left, left + slot, bottom)
            left += width
        return bottom + CELL + after

    return EvaluationPdfBlock(draw, keep_with_next=True)


def header_blocks(
    title: str,
    subject: str,
    start: str,
    end: str,
    logo: bytes | None,
    event: str | None = None,
    review_date: str | None = None,
) -> list[EvaluationPdfBlock]:
    """The title row, then the name, the event when there is one, the review
    period, and the review date once published — each on its ruled line."""
    fields: list[tuple[str, list[str]]] = [(NAME_LABEL, [subject])]
    if event:
        fields.append((EVENT_LABEL, [event]))
    fields.append((PERIOD_LABEL, [start, PERIOD_SEPARATOR, end]))
    if review_date:
        fields.append((REVIEW_DATE_LABEL, [review_date]))
    last = len(fields) - 1
    return [
        _title(title, logo),
        *(
            _field(label, parts, GAP if i == last else 0)
            for i, (label, parts) in enumerate(fields)
        ),
    ]
