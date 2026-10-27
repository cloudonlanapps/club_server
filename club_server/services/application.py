"""Application templates: storage and state moves (#93, applications R2–R5, R12–R14).

The lifecycle is an evaluation's: a draft is edited, saved (checked
complete and measured; the layout stored), published to members, and
unpublished or reverted to change it. Only a draft is edited; only a draft
or a saved template is deleted (soft).
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..club_calendar import club_today, day_to_date
from ..db.models.application import Application
from ..exceptions import (
    ApplicationNotFoundException,
    ApplicationTransitionException,
    InvalidStateException,
)
from ..schemas.application import (
    ENGLISH,
    ApplicationContent,
    ApplicationLayout,
    ApplicationResponse,
    ApplicationSpread,
    ApplicationStatus,
    ApplicationUpdate,
)
from ..schemas.common import PaginatedResponse
from ..utils import now_utc_ms
from .application_document import options_document, template_document
from .application_drawing import ApplicationDrawingService
from .application_form import decide_layout
from .application_labels import club_labels
from .application_languages import (
    check_offered,
    check_written,
    club_languages,
    kept,
    shown,
)
from .audit import AuditService
from .audit_actions import AuditAction
from .evaluation_pdf_logo import club_logo
from .profile_options import ProfileOptionsService

DRAFT, SAVED, PUBLISHED = "draft", "saved", "published"

# The moves a template can make: action → (from, to).
TRANSITIONS: dict[str, tuple[str, str]] = {
    "save": (DRAFT, SAVED),  # checked complete, layout decided and stored
    "publish": (SAVED, PUBLISHED),  # members see it; a new publish ID; drawn once
    "unpublish": (PUBLISHED, SAVED),  # its stored drawing is dropped
    "revert": (SAVED, DRAFT),  # editable again; the stored layout is dropped
}
# The audit action of each move (R24).
MOVE_ACTIONS: dict[str, AuditAction] = {
    "save": AuditAction.SAVE_APPLICATION,
    "publish": AuditAction.PUBLISH_APPLICATION,
    "unpublish": AuditAction.UNPUBLISH_APPLICATION,
    "revert": AuditAction.REVERT_APPLICATION,
}
DELETABLE = frozenset({DRAFT, SAVED})
CONTENT_FIELDS = tuple(ApplicationContent.model_fields)
LIST_FIELDS = frozenset(
    {"header", "age_categories", "selections", "terms", "terms_notes"}
)
DICT_FIELDS = frozenset({"facts", "label_overrides"})
# The fields holding printed texts, shown in the club's languages (#133).
TEXT_FIELDS = tuple(f for f in CONTENT_FIELDS if f not in ("languages", "facts"))


def _stored(value: Any) -> Any:
    """A schema value as its JSON column holds it: the API's shape."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_stored(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {k: _stored(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value


def _empty(field: str) -> Any:
    """What null stores: an empty list or map, English for the languages."""
    if field == "languages":
        return [ENGLISH]
    if field in LIST_FIELDS:
        return []
    if field in DICT_FIELDS:
        return {}
    return None


def application_response(
    application: Application,
    club: list[str],
    fitting_categories: list[int] | None = None,
) -> ApplicationResponse:
    """A template as returned, its texts in the club's languages ``club``
    (applications R30); a member's read adds the categories they fit."""
    content: dict[str, Any] = {
        field: shown(getattr(application, field), club)
        if field in TEXT_FIELDS
        else getattr(application, field)
        for field in CONTENT_FIELDS
    }
    stored = application.layout
    layout = (
        ApplicationLayout(
            name=stored["name"],
            spread=ApplicationSpread(**stored["spread"]),
        )
        if stored
        else None
    )
    status: ApplicationStatus = application.status  # pyright: ignore[reportAssignmentType]
    return ApplicationResponse.model_validate(
        {
            **ApplicationContent.model_validate(content).model_dump(),
            "id": application.id,
            "status": status,
            "layout": layout,
            "fitting_categories": fitting_categories,
            "publish_id": application.publish_id if status == PUBLISHED else None,
            "created_by": application.created_by,
            "created_at_utc": application.created_at,
            "updated_at_utc": application.updated_at,
        }
    )


class ApplicationService:
    """Templates and their lifecycle."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def get_or_raise(self, application_id: int) -> Application:
        """A live template, or ``ApplicationNotFoundException``."""
        result = await self.db.execute(
            select(Application).where(
                Application.id == application_id, Application.deleted_at.is_(None)
            )
        )
        application = result.scalar_one_or_none()
        if application is None:
            raise ApplicationNotFoundException(application_id)
        return application

    async def list_page(
        self, status: str | None, offset: int, limit: int
    ) -> PaginatedResponse[ApplicationResponse]:
        """Live templates, newest first, optionally of one status."""
        condition: list[ColumnElement[bool]] = [Application.deleted_at.is_(None)]
        if status is not None:
            condition.append(Application.status == status)
        total = await self.db.execute(
            select(func.count()).select_from(Application).where(*condition)
        )
        rows = await self.db.execute(
            select(Application)
            .where(*condition)
            .order_by(Application.created_at.desc(), Application.id.desc())
            .offset(offset)
            .limit(limit)
        )
        club = await club_languages(self.db)
        return PaginatedResponse(
            items=[application_response(a, club) for a in rows.scalars().all()],
            total=total.scalar_one(),
            offset=offset,
            limit=limit,
        )

    async def create(self, content: ApplicationContent, created_by: str) -> Application:
        """A new draft, printing in the club's languages (R2, R29)."""
        club = await club_languages(self.db)
        check_offered(content.languages, club)
        for field in TEXT_FIELDS:
            check_written(_stored(getattr(content, field)), club)
        now = now_utc_ms()
        application = Application(
            status=DRAFT,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            layout=None,
            **{field: _stored(getattr(content, field)) for field in CONTENT_FIELDS},
        )
        self.db.add(application)
        await self.db.flush()
        await self.db.refresh(application)
        return application

    async def update(
        self, application_id: int, changes: ApplicationUpdate
    ) -> Application:
        """Replace the fields sent; a draft only (R3). A text sent keeps its
        stored translations in languages the club has removed (R30)."""
        application = await self.get_or_raise(application_id)
        if application.status != DRAFT:
            raise InvalidStateException(
                f"Only a draft application can be edited (it is {application.status})"
            )
        club = await club_languages(self.db)
        if changes.languages is not None:
            check_offered(changes.languages, club)
        sent = {
            field: _stored(getattr(changes, field))
            for field in changes.model_fields_set
        }
        for field, new in sent.items():
            if field in TEXT_FIELDS:
                check_written(new, club)
        for field, new in sent.items():
            if new is None:
                setattr(application, field, _empty(field))
                continue
            if field in TEXT_FIELDS:
                new = kept(new, getattr(application, field), club)
            setattr(application, field, new)
        application.updated_at = now_utc_ms()
        await self.db.flush()
        await self.db.refresh(application)
        return application

    async def soft_delete(self, application_id: int) -> Application:
        """Soft delete a draft or saved template (R4)."""
        application = await self.get_or_raise(application_id)
        if application.status not in DELETABLE:
            raise InvalidStateException(
                "A published application must be unpublished before it is deleted"
            )
        now = now_utc_ms()
        application.deleted_at = now
        application.updated_at = now
        await self.db.flush()
        return application

    async def duplicate(self, application_id: int, created_by: str) -> Application:
        """A new draft copied from any template (R5)."""
        source = await self.get_or_raise(application_id)
        now = now_utc_ms()
        copy = Application(
            status=DRAFT,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            layout=None,
            **{field: getattr(source, field) for field in CONTENT_FIELDS},
        )
        self.db.add(copy)
        await self.db.flush()
        await self.db.refresh(copy)
        return copy

    async def move(
        self,
        application_id: int,
        action: str,
        actor_username: str,
        ip_address: str | None = None,
    ) -> Application:
        """One of the four state moves, audited (R12–R14, R22, R24)."""
        application = await self.get_or_raise(application_id)
        source, target = TRANSITIONS[action]
        if application.status != source:
            raise ApplicationTransitionException(application.status, action)
        details: dict[str, str | int | None] = {"applicationId": application.id}
        if action == "save":
            check_offered(list(application.languages), await club_languages(self.db))
            options = options_document(await ProfileOptionsService(self.db).read())
            application.layout = await asyncio.to_thread(
                decide_layout,
                template_document(application),
                options,
                day_to_date(club_today()),
                await club_labels(self.db),
            )
        if action == "revert":
            application.layout = None
        if action == "publish":
            details["previousPublishId"] = application.publish_id
            application.publish_id = str(uuid.uuid4())
            _ = await ApplicationDrawingService(self.db).store(
                application,
                await club_logo(self.db),
                options_document(await ProfileOptionsService(self.db).read()),
            )
        if action == "unpublish":
            await ApplicationDrawingService(self.db).drop(application.id)
        if action in ("publish", "unpublish"):
            details["publishId"] = application.publish_id
        application.status = target
        application.updated_at = now_utc_ms()
        await self.db.flush()
        await AuditService(self.db).log(
            actor_username=actor_username,
            action=MOVE_ACTIONS[action],
            resource_type="application",
            resource_id=str(application.id),
            details=details,
            ip_address=ip_address,
        )
        await self.db.refresh(application)
        return application
