"""Evaluation template service (#302, #535).

Templates are front matter, a layout, and items. Item edits and the
cross-template search live in ``evaluation_template_items.py``.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation
from ..db.models.evaluation_template import EvaluationTemplate
from ..db.models.evaluation_template_item import EvaluationTemplateItem
from ..exceptions import (
    EvaluationItemNotFoundException,
    EvaluationOriginMismatchException,
    EvaluationTemplateInUseException,
    EvaluationTemplateNameTakenException,
    EvaluationTemplateNotFoundException,
    HardDeleteNeedsSoftDeleteException,
    NothingToRestoreException,
)
from ..schemas.common import PaginatedResponse
from ..schemas.evaluation_item import EvaluationInfoItem, EvaluationTemplateItemSchema
from ..schemas.evaluation_template import (
    EvaluationLayoutEntry,
    EvaluationLayoutSectionInput,
    EvaluationTemplateCreate,
    EvaluationTemplateResponse,
)
from ..utils import now_utc_ms
from .evaluation_items import (
    answer_domain,
    check_layout,
    item_row,
    item_schema,
    layout_json,
    ordered_items,
)


def template_response(
    template: EvaluationTemplate, in_use: bool
) -> EvaluationTemplateResponse:
    """A template with its items in layout order."""
    return EvaluationTemplateResponse(
        in_use=in_use,
        id=template.id,
        name=template.name,
        created_by=template.created_by,
        layout=template.layout,
        items=[item_schema(r) for r in ordered_items(template.items, template.layout)],
        created_at_utc=template.created_at,
        updated_at_utc=template.updated_at,
        deleted_at_utc=template.deleted_at,
    )


class EvaluationTemplateService:
    """Templates are the validation contract evaluations are written against."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def get_or_raise(
        self, template_id: int, include_deleted: bool = False
    ) -> EvaluationTemplate:
        """Fetch a template or raise."""
        query = select(EvaluationTemplate).where(EvaluationTemplate.id == template_id)
        if not include_deleted:
            query = query.where(EvaluationTemplate.deleted_at.is_(None))
        result = await self.db.execute(query)
        template = result.scalar_one_or_none()
        if not template:
            raise EvaluationTemplateNotFoundException(template_id)
        return template

    async def _page(
        self, deleted: bool, offset: int, limit: int
    ) -> PaginatedResponse[EvaluationTemplateResponse]:
        """Shared paging for the live and deleted listings."""
        condition = (
            EvaluationTemplate.deleted_at.isnot(None)
            if deleted
            else EvaluationTemplate.deleted_at.is_(None)
        )
        total_result = await self.db.execute(
            select(func.count()).select_from(EvaluationTemplate).where(condition)
        )
        result = await self.db.execute(
            select(EvaluationTemplate)
            .where(condition)
            .order_by(
                EvaluationTemplate.created_at.desc(), EvaluationTemplate.id.desc()
            )
            .offset(offset)
            .limit(limit)
        )
        return PaginatedResponse(
            items=await self.responses(list(result.scalars().all())),
            total=total_result.scalar_one(),
            offset=offset,
            limit=limit,
        )

    async def responses(
        self, templates: list[EvaluationTemplate]
    ) -> list[EvaluationTemplateResponse]:
        """Templates as returned, each saying whether it is in use (R27a)."""
        ids = [t.id for t in templates]
        used: set[int] = set()
        if ids:
            rows = await self.db.execute(
                select(Evaluation.template_id)
                .where(Evaluation.template_id.in_(ids))
                .distinct()
            )
            used = set(rows.scalars().all())
        return [template_response(t, t.id in used) for t in templates]

    async def response(
        self, template: EvaluationTemplate
    ) -> EvaluationTemplateResponse:
        """One template as returned (R27a)."""
        [single] = await self.responses([template])
        return single

    async def list_templates(
        self, offset: int = 0, limit: int = 20
    ) -> PaginatedResponse[EvaluationTemplateResponse]:
        """List live templates."""
        return await self._page(deleted=False, offset=offset, limit=limit)

    async def list_deleted_templates(
        self, offset: int = 0, limit: int = 20
    ) -> PaginatedResponse[EvaluationTemplateResponse]:
        """List soft-deleted templates."""
        return await self._page(deleted=True, offset=offset, limit=limit)

    async def resolve_origin(self, item: EvaluationTemplateItemSchema) -> int | None:
        """The first item a copy descends from, after checking its domain (R12b)."""
        if isinstance(item, EvaluationInfoItem) or item.origin_item_id is None:
            return None
        source = await self.db.get(EvaluationTemplateItem, item.origin_item_id)
        if source is None:
            raise EvaluationItemNotFoundException(item.origin_item_id)
        origin = source
        if source.origin_item_id is not None:
            first = await self.db.get(EvaluationTemplateItem, source.origin_item_id)
            origin = first or source
        if answer_domain(item_schema(origin)) != answer_domain(item):
            raise EvaluationOriginMismatchException(origin.id)
        return origin.id

    async def refuse_taken_name(self, name: str, own_id: int | None = None) -> None:
        """A live template's name is unique, ignoring case and outer spaces (R49a).

        ``own_id`` is the template being renamed or restored, which does not
        collide with itself.
        """
        query = select(EvaluationTemplate.id).where(
            EvaluationTemplate.deleted_at.is_(None),
            func.lower(func.trim(EvaluationTemplate.name))
            == func.lower(func.trim(name)),
        )
        if own_id is not None:
            query = query.where(EvaluationTemplate.id != own_id)
        taken = await self.db.execute(query.limit(1))
        if taken.scalar_one_or_none() is not None:
            raise EvaluationTemplateNameTakenException(name.strip())

    async def create_template(
        self, payload: EvaluationTemplateCreate, created_by: str
    ) -> EvaluationTemplate:
        """Create a template whole: items inline in the layout get their ids here."""
        await self.refuse_taken_name(payload.name)
        now = now_utc_ms()
        template = EvaluationTemplate(
            name=payload.name,
            created_by=created_by,
            layout=[],
            created_at=now,
            updated_at=now,
        )
        self.db.add(template)
        await self.db.flush()

        layout: list[object] = []
        for entry in payload.layout:
            if isinstance(entry, EvaluationLayoutSectionInput):
                ids = [await self._insert(template.id, i) for i in entry.items]
                layout.append({"section": entry.section, "items": ids})
            else:
                layout.append(await self._insert(template.id, entry))
        template.layout = layout
        await self.db.flush()
        await self.db.refresh(template, ["items"])
        return template

    async def _insert(
        self, template_id: int, item: EvaluationTemplateItemSchema
    ) -> int:
        """Insert one item row and return its id."""
        row = item_row(item, await self.resolve_origin(item))
        row.template_id = template_id
        self.db.add(row)
        await self.db.flush()
        return row.id

    async def refuse_if_used(self, template_id: int) -> None:
        """Items and layout are frozen while any evaluation uses them (R27).

        Soft-deleted evaluations count too: a restore needs the contract it
        was written against (#486).
        """
        in_use = await self.count_evaluations(template_id, include_deleted=True)
        if in_use:
            raise EvaluationTemplateInUseException(template_id, in_use)

    async def update_template(
        self,
        template_id: int,
        name: str | None = None,
        layout: list[EvaluationLayoutEntry] | None = None,
    ) -> EvaluationTemplate:
        """Rename, which is always allowed, or re-lay out an unused template."""
        template = await self.get_or_raise(template_id)
        if layout is not None:
            await self.refuse_if_used(template_id)
            stored = layout_json(layout)
            check_layout(stored, {row.id for row in template.items})
            template.layout = stored
        if name is not None:
            await self.refuse_taken_name(name, own_id=template_id)
            template.name = name
        template.updated_at = now_utc_ms()
        await self.db.flush()
        await self.db.refresh(template)
        return template

    async def count_evaluations(
        self, template_id: int, include_deleted: bool = False
    ) -> int:
        """Evaluations validated against this template (R27).

        Live ones only, unless ``include_deleted``.
        """
        query = (
            select(func.count())
            .select_from(Evaluation)
            .where(Evaluation.template_id == template_id)
        )
        if not include_deleted:
            query = query.where(Evaluation.deleted_at.is_(None))
        result = await self.db.execute(query)
        return result.scalar_one()

    async def soft_delete_template(self, template_id: int) -> EvaluationTemplate:
        """Soft delete, refused while evaluations still reference it."""
        template = await self.get_or_raise(template_id)
        in_use = await self.count_evaluations(template_id)
        if in_use:
            raise EvaluationTemplateInUseException(template_id, in_use)

        now = now_utc_ms()
        template.deleted_at = now
        template.updated_at = now
        await self.db.flush()
        await self.db.refresh(template)
        return template

    async def restore_template(self, template_id: int) -> EvaluationTemplate:
        """Restore a soft-deleted template."""
        template = await self.get_or_raise(template_id, include_deleted=True)
        if template.deleted_at is None:
            raise NothingToRestoreException("Template", template_id)
        await self.refuse_taken_name(template.name, own_id=template_id)
        template.deleted_at = None
        template.updated_at = now_utc_ms()
        await self.db.flush()
        await self.db.refresh(template)
        return template

    async def hard_delete_template(self, template_id: int) -> None:
        """Hard delete a soft-deleted template. Super-admin only, never while referenced."""
        template = await self.get_or_raise(template_id, include_deleted=True)
        if template.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("Template", template_id)
        in_use = await self.count_evaluations(template_id, include_deleted=True)
        if in_use:
            raise EvaluationTemplateInUseException(template_id, in_use)
        await self.db.delete(template)
        await self.db.flush()
