"""The member view as the PDF's blocks, in layout order (#535, R63, R64).

Ported from the prototype's ``survey_pdf_table.dart`` and
``survey_pdf_blocks.dart``: a table head, then each section's bar and every
question, one row per block; info text runs the table's full width. A
written (Q & A) answer takes its row like any other, except the run of
Q & A questions closing the layout at its top level, which prints after the
table as written blocks (R64a).
"""

from ..schemas.evaluation import EvaluationAnswerResponse, EvaluationMemberView
from ..schemas.evaluation_item import (
    EvaluationInfoItem,
    EvaluationQaItem,
    EvaluationTemplateItemSchema,
)
from ..schemas.evaluation_template import EvaluationLayoutSection
from .evaluation_pdf_answer import answer_display
from .evaluation_pdf_block import EvaluationPdfBlock
from .evaluation_pdf_blocks import written_block
from .evaluation_pdf_cells import bar, draw_head, full_width, question_row
from .evaluation_pdf_layout import EVIDENCE_LABEL, EVIDENCE_PATH, LIST_SEPARATOR
from .evaluation_pdf_markdown import PdfTextRun, markdown_runs


def evidence_runs(
    answer: EvaluationAnswerResponse | None, base_url: str
) -> list[PdfTextRun]:
    """The word Evidence linked to each file of an answer (R64)."""
    if answer is None or not answer.evidence:
        return []
    many = len(answer.evidence) > 1
    runs: list[PdfTextRun] = []
    for number, file in enumerate(answer.evidence, start=1):
        if runs:
            runs.append(PdfTextRun(LIST_SEPARATOR))
        label = f"{EVIDENCE_LABEL} {number}" if many else EVIDENCE_LABEL
        runs.append(
            PdfTextRun(label, base_url + EVIDENCE_PATH.format(uuid=file.media_uuid))
        )
    return runs


def closing_qa_count(view: EvaluationMemberView) -> int:
    """How many top-level Q & A entries close the layout (R64a)."""
    items = {item.id: item for item in view.template.items}
    count = 0
    for entry in reversed(view.template.layout):
        if not isinstance(entry, int) or not isinstance(items[entry], EvaluationQaItem):
            break
        count += 1
    return count


class _Flow:
    """Collects the table's blocks and the written answers that follow it."""

    def __init__(self, view: EvaluationMemberView, base_url: str):
        self.items = {item.id: item for item in view.template.items}
        self.answers = {answer.item_id: answer for answer in view.answers}
        self.base_url = base_url
        self.table: list[EvaluationPdfBlock] = []
        self.written: list[EvaluationPdfBlock] = []

    def written_answer(self, item: EvaluationQaItem) -> None:
        """A closing Q & A: its question over the handwritten answer."""
        answer = self.answers.get(item.id) if item.id is not None else None
        text = answer.value_text if answer is not None else None
        self.written.append(written_block(item.question, text))

    def add(self, item: EvaluationTemplateItemSchema, section: int | None) -> None:
        answer = self.answers.get(item.id) if item.id is not None else None
        if isinstance(item, EvaluationInfoItem):
            draw = full_width(markdown_runs(item.markdown))
        else:
            draw = question_row(
                item.question,
                evidence_runs(answer, self.base_url),
                answer_display(item, answer),
                answer.coach_note if answer is not None else None,
            )
        self.table.append(EvaluationPdfBlock(draw, table_part=True, section=section))

    def section(self, entry: EvaluationLayoutSection) -> None:
        # Numbered by its first block, so each section has its own id.
        own = len(self.table)
        self.table.append(
            EvaluationPdfBlock(
                bar(entry.section), keep_with_next=True, table_part=True, section=own
            )
        )
        for item_id in entry.items:
            self.add(self.items[item_id], own)


def body_blocks(view: EvaluationMemberView, base_url: str) -> list[EvaluationPdfBlock]:
    """The table (when it has rows) and then the closing written answers."""
    flow = _Flow(view, base_url)
    layout = view.template.layout
    table_end = len(layout) - closing_qa_count(view)
    for entry in layout[:table_end]:
        if isinstance(entry, int):
            flow.add(flow.items[entry], None)
        else:
            flow.section(entry)
    for entry in layout[table_end:]:
        item = flow.items[entry]
        if isinstance(item, EvaluationQaItem):
            flow.written_answer(item)
    if not flow.table:
        return flow.written
    head = EvaluationPdfBlock(draw_head, keep_with_next=True)
    return [head, *flow.table, *flow.written]
