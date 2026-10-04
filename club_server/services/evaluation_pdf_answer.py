"""Which drawing an answer gets in the member copy PDF (#535, R63).

Ported from the prototype's ``survey_pdf_answer.dart`` and
``survey_answer_text.dart``: stars for a star rating, a pie with
"value/max" for a range, otherwise the answer's label or value as text.
"""

from ..schemas.evaluation import EvaluationAnswerResponse
from ..schemas.evaluation_item import (
    EvaluationChoiceItem,
    EvaluationNumberItem,
    EvaluationQaItem,
    EvaluationRatingItem,
    EvaluationTemplateItemSchema,
    EvaluationYesNoItem,
)
from .evaluation_pdf_answer_kinds import (
    EvaluationPdfAnswer,
    NotAnswered,
    PieAnswer,
    StarsAnswer,
    TextAnswer,
)
from .evaluation_pdf_layout import LIST_SEPARATOR, NO, RANGE_TEXT, YES
from .evaluation_pdf_text import number_text

STARS = "stars"


def _rating(item: EvaluationRatingItem, value: float) -> EvaluationPdfAnswer:
    if item.rate_values is not None:
        levels = {level.value: level.text for level in item.rate_values}
        return TextAnswer(levels.get(int(value), number_text(value)))
    scale = item.scale()
    if item.rate_type == STARS:
        return StarsAnswer(len(scale), sum(1 for step in scale if step <= value))
    top = scale[-1]
    if top > 0:
        return PieAnswer(int(value), top)
    return TextAnswer(RANGE_TEXT.format(value=number_text(value), max=top))


def _choices(item: EvaluationChoiceItem, picked: list[str]) -> str:
    """The chosen labels, in the order the question lists its choices."""
    chosen = set(picked)
    labels = [choice.text for choice in item.choices if choice.value in chosen]
    known = {choice.value for choice in item.choices}
    labels += [value for value in picked if value not in known]
    return LIST_SEPARATOR.join(labels)


def answer_display(
    item: EvaluationTemplateItemSchema, answer: EvaluationAnswerResponse | None
) -> EvaluationPdfAnswer:
    """Stars, a pie, a label or "Not answered" for one answer."""
    if answer is None:
        return NotAnswered()
    value = answer.value_num
    if isinstance(item, EvaluationRatingItem) and value is not None:
        return _rating(item, value)
    if isinstance(item, EvaluationYesNoItem) and value is not None:
        return TextAnswer(
            (item.label_true or YES) if value else (item.label_false or NO)
        )
    if isinstance(item, EvaluationChoiceItem):
        picked = [answer.value_text] if answer.value_text else answer.choices
        if picked:
            return TextAnswer(_choices(item, picked))
    if isinstance(item, EvaluationNumberItem) and value is not None:
        return TextAnswer(number_text(value))
    if isinstance(item, EvaluationQaItem) and answer.value_text:
        return TextAnswer(answer.value_text)
    return NotAnswered()
