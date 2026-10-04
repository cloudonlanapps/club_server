"""Pure helpers for template items and layouts (#535).

Between the API and the database an item is split into columns and the
``element`` JSONB of its variant, and joined back on read (R12). A layout is
stored as item ids and ``{"section", "items"}`` groups (R12c).
"""

from typing import Any, cast

from ..db.models.evaluation_template_item import (
    EvaluationItemType,
    EvaluationTemplateItem,
)
from ..exceptions import EvaluationLayoutInvalidException
from ..schemas.evaluation_item import (
    EvaluationChoiceItem,
    EvaluationInfoItem,
    EvaluationRatingItem,
    EvaluationTemplateItemSchema,
    template_item_adapter,
)
from ..schemas.evaluation_template import EvaluationLayoutEntry, EvaluationLayoutSection

# The fields kept in columns rather than in ``element``.
_COLUMN_FIELDS = {"id", "type", "question", "is_private", "origin_item_id"}


def item_row(
    item: EvaluationTemplateItemSchema, origin_item_id: int | None
) -> EvaluationTemplateItem:
    """A new row for an item, with its resolved origin."""
    row = EvaluationTemplateItem()
    fill_row(row, item, origin_item_id)
    return row


def fill_row(
    row: EvaluationTemplateItem,
    item: EvaluationTemplateItemSchema,
    origin_item_id: int | None,
) -> None:
    """Write an item's columns and element onto a row."""
    row.type = item.type
    row.question = None if isinstance(item, EvaluationInfoItem) else item.question
    row.is_private = item.is_private
    row.origin_item_id = origin_item_id
    row.element = item.model_dump(
        by_alias=True, mode="json", exclude=_COLUMN_FIELDS, exclude_none=True
    )


def item_schema(row: EvaluationTemplateItem) -> EvaluationTemplateItemSchema:
    """Rebuild the API variant from a row."""
    data: dict[str, Any] = {
        "id": row.id,
        "type": row.type,
        "isPrivate": row.is_private,
        **row.element,
    }
    if row.type != EvaluationItemType.info.value:
        data["question"] = row.question
        data["originItemId"] = row.origin_item_id
    return template_item_adapter.validate_python(data)


def is_question(row: EvaluationTemplateItem) -> bool:
    """True for an item that takes an answer."""
    return row.type != EvaluationItemType.info.value


def answer_domain(item: EvaluationTemplateItemSchema) -> tuple[Any, ...]:
    """What answers an item can take: the same domain means comparable (R12b)."""
    if isinstance(item, EvaluationRatingItem):
        return (item.type, tuple(item.scale()))
    if isinstance(item, EvaluationChoiceItem):
        return (item.type, tuple(sorted(c.value for c in item.choices)))
    return (item.type,)


def _section(entry: Any) -> tuple[str, list[int]] | None:
    """A stored section's title and item ids, or None for a bare item id."""
    if isinstance(entry, dict):
        section = cast(dict[str, Any], entry)
        return str(section["section"]), [int(i) for i in section["items"]]
    return None


def layout_ids(layout: list[Any]) -> list[int]:
    """Every item id a stored layout names, in order."""
    ids: list[int] = []
    for entry in layout:
        section = _section(entry)
        ids.extend(section[1] if section else [int(entry)])
    return ids


def layout_json(layout: list[EvaluationLayoutEntry]) -> list[Any]:
    """A validated layout as it is stored."""
    return [
        entry.model_dump(mode="json")
        if isinstance(entry, EvaluationLayoutSection)
        else entry
        for entry in layout
    ]


def check_layout(layout: list[Any], item_ids: set[int]) -> None:
    """Every item of the template exactly once, and no other id (R12c)."""
    named = layout_ids(layout)
    if len(named) != len(set(named)):
        raise EvaluationLayoutInvalidException("an item appears more than once")
    if set(named) != item_ids:
        raise EvaluationLayoutInvalidException(
            "the layout must name every item of the template, and only those"
        )


def append_to_layout(layout: list[Any], item_id: int, section: str | None) -> list[Any]:
    """A copy of the layout with the item added at the end, or to the section."""
    updated: list[Any] = []
    placed = section is None
    for entry in layout:
        found = _section(entry)
        if found is None:
            updated.append(entry)
            continue
        title, items = found
        if not placed and title == section:
            items.append(item_id)
            placed = True
        updated.append({"section": title, "items": items})
    if section is None:
        updated.append(item_id)
    elif not placed:
        updated.append({"section": section, "items": [item_id]})
    return updated


def remove_from_layout(layout: list[Any], item_id: int) -> list[Any]:
    """A copy of the layout without the item; sections stay, even if emptied."""
    updated: list[Any] = []
    for entry in layout:
        found = _section(entry)
        if found is None:
            if entry != item_id:
                updated.append(entry)
            continue
        title, items = found
        updated.append({"section": title, "items": [i for i in items if i != item_id]})
    return updated


def public_layout(layout: list[Any], public_ids: set[int]) -> list[Any]:
    """The layout without private items, and without the sections they emptied (R39)."""
    kept: list[Any] = []
    for entry in layout:
        found = _section(entry)
        if found is None:
            if entry in public_ids:
                kept.append(entry)
            continue
        title, items = found
        visible = [i for i in items if i in public_ids]
        if visible:
            kept.append({"section": title, "items": visible})
    return kept


def ordered_items(
    rows: list[EvaluationTemplateItem], layout: list[Any]
) -> list[EvaluationTemplateItem]:
    """Rows in layout order."""
    by_id = {row.id: row for row in rows}
    return [by_id[i] for i in layout_ids(layout) if i in by_id]
