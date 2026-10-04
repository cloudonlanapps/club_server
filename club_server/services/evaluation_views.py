"""The two projections of an evaluation (#535, R39, R41).

The staff view is the whole evaluation, for its effective owner. The member
view is built separately, from the public items alone, so a private item —
its question, answer, note and evidence — has no route to the member.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation
from ..db.models.evaluation_answer import EvaluationAnswer
from ..db.models.evaluation_template import EvaluationTemplate
from ..schemas.evaluation import (
    EvaluationAnswerResponse,
    EvaluationMemberTemplate,
    EvaluationMemberView,
    EvaluationStaffView,
)
from .evaluation_items import item_schema, ordered_items, public_layout
from .evaluation_media import evidence_by_item


async def answers_of(
    db: AsyncSession, evaluation_id: int, keep: set[int] | None = None
) -> list[EvaluationAnswerResponse]:
    """Every answer — a value, a note or evidence — in item order; only ``keep`` if given."""
    result = await db.execute(
        select(EvaluationAnswer)
        .where(EvaluationAnswer.evaluation_id == evaluation_id)
        .execution_options(populate_existing=True)
    )
    rows = {row.item_id: row for row in result.scalars().all()}
    evidence = await evidence_by_item(db, evaluation_id)
    item_ids = sorted(set(rows) | set(evidence))
    answers: list[EvaluationAnswerResponse] = []
    for item_id in item_ids:
        if keep is not None and item_id not in keep:
            continue
        row = rows.get(item_id)
        answers.append(
            EvaluationAnswerResponse(
                item_id=item_id,
                value_num=row.value_num if row else None,
                value_text=row.value_text if row else None,
                choices=sorted(c.value for c in row.choices) if row else [],
                coach_note=row.coach_note if row else None,
                evidence=evidence.get(item_id, []),
            )
        )
    return answers


async def staff_view(db: AsyncSession, evaluation: Evaluation) -> EvaluationStaffView:
    """The whole evaluation, private items included (R41)."""
    return EvaluationStaffView(
        id=evaluation.id,
        template_id=evaluation.template_id,
        created_for=evaluation.created_for,
        created_by=evaluation.created_by,
        owner=evaluation.owner,
        event_id=evaluation.event_id,
        period_start_utc=evaluation.period_start_utc,
        period_end_utc=evaluation.period_end_utc,
        status=evaluation.status,
        answers=await answers_of(db, evaluation.id),
        created_at_utc=evaluation.created_at,
        updated_at_utc=evaluation.updated_at,
        published_at_utc=evaluation.published_at,
        deleted_at_utc=evaluation.deleted_at,
    )


async def member_view(db: AsyncSession, evaluation: Evaluation) -> EvaluationMemberView:
    """A published evaluation as its member sees it: public items only (R39)."""
    template = await db.get(EvaluationTemplate, evaluation.template_id)
    assert template is not None  # RESTRICT keeps a used template in place
    public = [row for row in template.items if not row.is_private]
    public_ids = {row.id for row in public}
    layout = public_layout(template.layout, public_ids)
    return EvaluationMemberView(
        id=evaluation.id,
        created_for=evaluation.created_for,
        created_by=evaluation.created_by,
        owner=evaluation.owner,
        event_id=evaluation.event_id,
        period_start_utc=evaluation.period_start_utc,
        period_end_utc=evaluation.period_end_utc,
        status=evaluation.status,
        published_at_utc=evaluation.published_at,
        template=EvaluationMemberTemplate(
            id=template.id,
            name=template.name,
            layout=layout,
            items=[item_schema(row) for row in ordered_items(public, layout)],
        ),
        answers=await answers_of(db, evaluation.id, keep=public_ids),
    )
