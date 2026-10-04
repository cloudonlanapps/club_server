"""Answers: validated against their item, written one at a time (#535).

R9: an answer is a value, a coach note, evidence — any of them alone.
R10: the value fits the item's type and domain, or → ``INVALID_ANSWER``.
R15: saving needs every required question answered, and the coach note
     wherever the answer given requires one.
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation, EvaluationMediaLink
from ..db.models.evaluation_answer import EvaluationAnswer, EvaluationAnswerChoice
from ..db.models.evaluation_template_item import EvaluationTemplateItem
from ..exceptions import EvaluationAnswerInvalidException
from ..schemas.evaluation import EvaluationAnswerInput
from ..schemas.evaluation_item import (
    EvaluationChoiceItem,
    EvaluationInfoItem,
    EvaluationNumberItem,
    EvaluationQaItem,
    EvaluationRatingItem,
    EvaluationTemplateItemSchema,
    EvaluationYesNoItem,
)
from ..utils import now_utc_ms
from .evaluation_items import item_schema

YES = 1
NO = 0


def _invalid(reason: str) -> EvaluationAnswerInvalidException:
    return EvaluationAnswerInvalidException(reason)


def _only(answer: EvaluationAnswerInput, allowed: str) -> None:
    """Refuse a value in any field but the one the item's type uses."""
    given = {
        "valueNum": answer.value_num is not None,
        "valueText": answer.value_text is not None,
        "choices": answer.choices is not None,
    }
    wrong = [name for name, present in given.items() if present and name != allowed]
    if wrong:
        raise _invalid(f"this question is answered with {allowed}, not {wrong[0]}")


def validate_answer(
    item: EvaluationTemplateItemSchema, answer: EvaluationAnswerInput
) -> None:
    """Raise unless the answer fits its item (R10)."""
    if isinstance(item, EvaluationInfoItem):
        raise _invalid("an info item takes no answer")
    has_value = (
        answer.value_num is not None
        or answer.value_text is not None
        or answer.choices is not None
    )
    if not has_value and answer.coach_note is None:
        raise _invalid("an answer needs a value or a coach note")

    if isinstance(item, EvaluationRatingItem):
        _only(answer, "valueNum")
        value = answer.value_num
        if value is not None and (
            value != int(value) or int(value) not in item.scale()
        ):
            raise _invalid("the rating is outside the question's scale")
    elif isinstance(item, EvaluationYesNoItem):
        _only(answer, "valueNum")
        if answer.value_num is not None and answer.value_num not in (YES, NO):
            raise _invalid("a Yes / No answer is 1 or 0")
    elif isinstance(item, EvaluationNumberItem):
        _only(answer, "valueNum")
    elif isinstance(item, EvaluationQaItem):
        _only(answer, "valueText")
    elif item.type == "singleChoice":
        _only(answer, "valueText")
        values = {c.value for c in item.choices}
        if answer.value_text is not None and answer.value_text not in values:
            raise _invalid("the answer is not one of the question's choices")
    else:
        _only(answer, "choices")
        _check_selection(item, answer.choices)


def _check_selection(item: EvaluationChoiceItem, choices: list[str] | None) -> None:
    """A multiple-choice answer: one or more of its choices, none twice."""
    if choices is None:
        return
    values = {c.value for c in item.choices}
    if not choices or len(choices) != len(set(choices)):
        raise _invalid("choose one or more choices, each once")
    if not set(choices) <= values:
        raise _invalid("the answer names a choice the question does not have")


def _has_value(answer: EvaluationAnswer) -> bool:
    return (
        answer.value_num is not None
        or answer.value_text is not None
        or bool(answer.choices)
    )


def _note_required(
    item: EvaluationTemplateItemSchema, answer: EvaluationAnswer
) -> bool:
    """True when the answer given is one the question wants a coach note for."""
    if isinstance(item, (EvaluationInfoItem, EvaluationQaItem)):
        return False
    required = item.require_comment_for
    if not item.show_comment_area or not required:
        return False
    if isinstance(item, EvaluationYesNoItem):
        return answer.value_num is not None and bool(answer.value_num) in required
    if isinstance(item, EvaluationChoiceItem):
        picked = (
            {answer.value_text}
            if answer.value_text
            else {c.value for c in answer.choices}
        )
        return bool(picked & set(required))
    return answer.value_num is not None and answer.value_num in required


def incomplete_items(
    items: list[EvaluationTemplateItem], answers: list[EvaluationAnswer]
) -> list[int]:
    """The questions that block saving, in item order (R15)."""
    by_item = {a.item_id: a for a in answers}
    missing: list[int] = []
    for row in items:
        item = item_schema(row)
        if isinstance(item, EvaluationInfoItem):
            continue
        answer = by_item.get(row.id)
        if answer is None or not _has_value(answer):
            if item.is_required:
                missing.append(row.id)
            continue
        if _note_required(item, answer) and not (answer.coach_note or "").strip():
            missing.append(row.id)
    return missing


class EvaluationAnswerService:
    """Writes and clears one answer of a draft."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def _item(
        self, evaluation: Evaluation, item_id: int
    ) -> EvaluationTemplateItem:
        """A question of the evaluation's own template, or ``INVALID_ANSWER``."""
        row = await self.db.get(EvaluationTemplateItem, item_id)
        if row is None or row.template_id != evaluation.template_id:
            raise _invalid(f"item {item_id} is not a question of this template")
        return row

    async def put_answer(
        self, evaluation: Evaluation, item_id: int, answer: EvaluationAnswerInput
    ) -> None:
        """Write one answer, replacing any earlier one (R9, R10, R21)."""
        validate_answer(item_schema(await self._item(evaluation, item_id)), answer)
        # Replace by statement: re-inserting a selection the answer already
        # had would collide on the choice table's key inside one flush.
        _ = await self.db.execute(
            delete(EvaluationAnswer).where(
                EvaluationAnswer.evaluation_id == evaluation.id,
                EvaluationAnswer.item_id == item_id,
            )
        )
        self.db.add(
            EvaluationAnswer(
                evaluation_id=evaluation.id,
                item_id=item_id,
                value_num=answer.value_num,
                value_text=answer.value_text,
                coach_note=answer.coach_note,
                choices=[EvaluationAnswerChoice(value=v) for v in answer.choices or []],
            )
        )
        evaluation.updated_at = now_utc_ms()
        await self.db.flush()

    async def clear_answer(self, evaluation: Evaluation, item_id: int) -> None:
        """Clear one answer and detach its evidence (R21, R22a)."""
        _ = await self.db.execute(
            delete(EvaluationAnswer).where(
                EvaluationAnswer.evaluation_id == evaluation.id,
                EvaluationAnswer.item_id == item_id,
            )
        )
        _ = await self.db.execute(
            delete(EvaluationMediaLink).where(
                EvaluationMediaLink.evaluation_id == evaluation.id,
                EvaluationMediaLink.tag == str(item_id),
            )
        )
        evaluation.updated_at = now_utc_ms()
        await self.db.flush()
