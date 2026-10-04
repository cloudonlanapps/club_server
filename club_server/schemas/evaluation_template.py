"""Evaluation template schemas (#302, #535)."""

from typing import Annotated, ClassVar

from pydantic import ConfigDict, Field, model_validator

from .common import CamelCaseModel
from .evaluation_item import EvaluationInfoItem, EvaluationTemplateItemSchema


class EvaluationLayoutSection(CamelCaseModel):
    """A titled group of item ids. Sections do not nest (R12c)."""

    section: str = Field(min_length=1)
    items: list[int]


EvaluationLayoutEntry = int | EvaluationLayoutSection


class EvaluationLayoutSectionInput(CamelCaseModel):
    """A titled group of new items, on create."""

    section: str = Field(min_length=1)
    items: list[EvaluationTemplateItemSchema]


EvaluationLayoutEntryInput = Annotated[
    EvaluationTemplateItemSchema | EvaluationLayoutSectionInput,
    Field(union_mode="left_to_right"),
]


class EvaluationTemplateCreate(CamelCaseModel):
    """Create a template whole: its items arrive inline in the layout (R49)."""

    name: str = Field(min_length=1)
    layout: list[EvaluationLayoutEntryInput]

    def flat_items(self) -> list[EvaluationTemplateItemSchema]:
        """Every item, in layout order."""
        items: list[EvaluationTemplateItemSchema] = []
        for entry in self.layout:
            if isinstance(entry, EvaluationLayoutSectionInput):
                items.extend(entry.items)
            else:
                items.append(entry)
        return items

    @model_validator(mode="after")
    def check_has_question(self) -> "EvaluationTemplateCreate":
        """A template that asks nothing cannot evaluate anything (R49)."""
        if all(isinstance(item, EvaluationInfoItem) for item in self.flat_items()):
            raise ValueError("a template needs at least one question")
        return self


class EvaluationTemplateUpdate(CamelCaseModel):
    """Rename, or re-lay out. Omitted fields are left unchanged."""

    name: str | None = Field(default=None, min_length=1)
    layout: list[EvaluationLayoutEntry] | None = None


class EvaluationTemplateItemAdd(CamelCaseModel):
    """Add one item, appended to the layout or to the named section (R26a)."""

    item: EvaluationTemplateItemSchema
    section: str | None = Field(default=None, min_length=1)


class EvaluationTemplateResponse(CamelCaseModel):
    """A template: front matter, layout, and its items in layout order."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    id: int
    name: str
    created_by: str
    layout: list[EvaluationLayoutEntry]
    items: list[EvaluationTemplateItemSchema]
    # Whether any evaluation, soft-deleted included, is written against it:
    # its items and layout are then frozen (R27, R27a).
    in_use: bool
    created_at_utc: int
    updated_at_utc: int
    deleted_at_utc: int | None = None


class EvaluationTemplateItemHit(CamelCaseModel):
    """One item found by the cross-template search, and where it lives (R46a)."""

    template_id: int
    template_name: str
    item: EvaluationTemplateItemSchema
