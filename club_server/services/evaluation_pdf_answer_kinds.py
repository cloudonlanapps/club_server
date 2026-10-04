"""How an answer is drawn in the member copy PDF (#535, R63).

One small value type per kind, so the drawing code matches on the kind and
the deciding code (``evaluation_pdf_answer``) stays pure and testable.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StarsAnswer:
    """``count`` stars, the first ``filled`` of them filled (a star rating)."""

    count: int
    filled: int


@dataclass(frozen=True)
class PieAnswer:
    """A ring filled ``value`` / ``max`` of the way round (a range rating)."""

    value: int
    max: int


@dataclass(frozen=True)
class TextAnswer:
    """The answer as handwritten text: a label, choices or a number."""

    text: str


@dataclass(frozen=True)
class NotAnswered:
    """No value was given."""


EvaluationPdfAnswer = StarsAnswer | PieAnswer | TextAnswer | NotAnswered
