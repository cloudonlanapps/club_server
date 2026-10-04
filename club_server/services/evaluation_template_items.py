"""Editing a template one item at a time, and finding items to copy (#535).

R26a: add, replace or remove one item; adding places it in the layout,
removing takes it out; an item's type is fixed. R27: none of it while an
evaluation uses the template. R46a: the cross-template search.
"""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation_template import EvaluationTemplate
from ..db.models.evaluation_template_item import EvaluationTemplateItem
from ..exceptions import (
    EvaluationItemNotFoundException,
    EvaluationItemTypeFixedException,
)
from ..schemas.common import PaginatedResponse
from ..schemas.evaluation_item import EvaluationInfoItem, EvaluationTemplateItemSchema
from ..schemas.evaluation_template import EvaluationTemplateItemHit
from ..utils import now_utc_ms
from .evaluation_items import (
    append_to_layout,
    fill_row,
    item_row,
    item_schema,
    remove_from_layout,
)
from .evaluation_template import EvaluationTemplateService


class EvaluationTemplateItemService:
    """Piecewise item edits on an unused template, and the item search."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self.templates: EvaluationTemplateService = EvaluationTemplateService(db)

    async def _unused_template(self, template_id: int) -> EvaluationTemplate:
        """A live template no evaluation uses, or raise."""
        template = await self.templates.get_or_raise(template_id)
        await self.templates.refuse_if_used(template_id)
        return template

    @staticmethod
    def _item_of(template: EvaluationTemplate, item_id: int) -> EvaluationTemplateItem:
        """The template's own item, or raise."""
        for row in template.items:
            if row.id == item_id:
                return row
        raise EvaluationItemNotFoundException(item_id)

    async def _touch(self, template: EvaluationTemplate) -> EvaluationTemplate:
        template.updated_at = now_utc_ms()
        await self.db.flush()
        await self.db.refresh(template)
        return template

    async def add_item(
        self,
        template_id: int,
        item: EvaluationTemplateItemSchema,
        section: str | None,
    ) -> tuple[EvaluationTemplate, int]:
        """Add one item, at the end of the layout or of the named section.

        Returns the template and the new item's id.
        """
        template = await self._unused_template(template_id)
        row = item_row(item, await self.templates.resolve_origin(item))
        row.template_id = template.id
        self.db.add(row)
        await self.db.flush()
        template.layout = append_to_layout(template.layout, row.id, section)
        return await self._touch(template), row.id

    async def replace_item(
        self, template_id: int, item_id: int, item: EvaluationTemplateItemSchema
    ) -> EvaluationTemplate:
        """Replace one item in place; it keeps its id, type and origin."""
        template = await self._unused_template(template_id)
        row = self._item_of(template, item_id)
        if item.type != row.type:
            raise EvaluationItemTypeFixedException(item_id)
        if not isinstance(item, EvaluationInfoItem):
            # The origin is the row's, whatever the request says; a copy keeps
            # its origin's domain on every write (R12b).
            item = item.model_copy(update={"origin_item_id": row.origin_item_id})
        fill_row(row, item, await self.templates.resolve_origin(item))
        return await self._touch(template)

    async def remove_item(self, template_id: int, item_id: int) -> EvaluationTemplate:
        """Remove one item and its layout entry."""
        template = await self._unused_template(template_id)
        row = self._item_of(template, item_id)
        template.layout = remove_from_layout(template.layout, item_id)
        template.items.remove(row)
        await self.db.flush()
        return await self._touch(template)

    async def search_items(
        self,
        search: str | None,
        item_type: str | None,
        offset: int,
        limit: int,
    ) -> PaginatedResponse[EvaluationTemplateItemHit]:
        """Items of every live template, by text and by type, newest template first."""
        conditions: list[ColumnElement[bool]] = [
            EvaluationTemplate.deleted_at.is_(None)
        ]
        if search:
            pattern = f"%{search}%"
            conditions.append(
                EvaluationTemplateItem.question.ilike(pattern)
                | EvaluationTemplateItem.element["markdown"].astext.ilike(pattern)
            )
        if item_type:
            conditions.append(EvaluationTemplateItem.type == item_type)
        base = (
            select(EvaluationTemplateItem, EvaluationTemplate)
            .join(
                EvaluationTemplate,
                EvaluationTemplate.id == EvaluationTemplateItem.template_id,
            )
            .where(*conditions)
        )
        total = await self.db.execute(select(func.count()).select_from(base.subquery()))
        rows = await self.db.execute(
            base.order_by(EvaluationTemplate.id.desc(), EvaluationTemplateItem.id)
            .offset(offset)
            .limit(limit)
        )
        return PaginatedResponse(
            items=[
                EvaluationTemplateItemHit(
                    template_id=template.id,
                    template_name=template.name,
                    item=item_schema(item),
                )
                for item, template in rows.all()
            ],
            total=total.scalar_one(),
            offset=offset,
            limit=limit,
        )
