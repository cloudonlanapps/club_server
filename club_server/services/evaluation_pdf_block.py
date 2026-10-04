"""One unit of the member copy PDF's flow (#535, R63)."""

from dataclasses import dataclass

from .evaluation_pdf_canvas import Draw


@dataclass(frozen=True)
class EvaluationPdfBlock:
    """Something drawn whole on one page, and how it may break from its neighbours.

    ``keep_with_next`` keeps it on the page of the block after it; a
    ``table_part`` starting a page gets the top line the part above it would
    have drawn; blocks sharing a ``section`` move to a fresh page together
    when they fit on one.
    """

    draw: Draw
    keep_with_next: bool = False
    table_part: bool = False
    section: int | None = None
