"""Who sees which application, and their forms as PDFs (#93, R10–R11, R19–R21).

An admin sees every template and downloads any member's form of a
published one. A member who has uploaded an identity document sees the
published templates and downloads their own form; without a document none
is offered. The form is refused when no age category matches the member.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..club_calendar import club_today, day_to_date
from ..db.models.application import Application
from ..db.models.media import Media
from ..db.models.media_links import UserMediaLink
from ..db.models.user import User
from ..dependencies import is_admin
from ..exceptions import (
    ApplicationNotFoundException,
    ApplicationNotPublishedException,
    IdentityDocumentRequiredException,
)
from ..schemas.application import ApplicationResponse
from ..schemas.common import PaginatedResponse
from .application import PUBLISHED, ApplicationService
from .application_catalogue import JsonDict
from .application_document import member_document, options_document, template_document
from .application_form import draw, frozen_options
from .application_rules import fitting_categories, preview_member
from .evaluation_pdf_logo import club_logo
from .profile_options import ProfileOptionsService
from .user import IDENTITY_DOCUMENT_TAG, UserService


class ApplicationDownloadService:
    """What each caller is offered, and the PDFs."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._applications: ApplicationService = ApplicationService(db)

    async def has_identity_document(self, username: str) -> bool:
        """Whether ``username`` has a live identity document (R10)."""
        result = await self.db.execute(
            select(UserMediaLink.media_uuid)
            .join(Media, Media.uuid == UserMediaLink.media_uuid)
            .where(
                UserMediaLink.username == username,
                UserMediaLink.tag == IDENTITY_DOCUMENT_TAG,
                Media.deleted_at.is_(None),
            )
            .limit(1)
        )
        return result.first() is not None

    async def list_for(
        self, caller: User, status: str | None, offset: int, limit: int
    ) -> PaginatedResponse[ApplicationResponse]:
        """Admins: every template (of ``status``); members: the published ones."""
        if is_admin(caller):
            return await self._applications.list_page(status, offset, limit)
        if not await self.has_identity_document(caller.username):
            return PaginatedResponse(items=[], total=0, offset=offset, limit=limit)
        return await self._applications.list_page(PUBLISHED, offset, limit)

    async def get_for(self, caller: User, application_id: int) -> Application:
        """One template as ``caller`` may see it."""
        application = await self._applications.get_or_raise(application_id)
        if is_admin(caller):
            return application
        if application.status != PUBLISHED:
            raise ApplicationNotFoundException(application_id)
        if not await self.has_identity_document(caller.username):
            raise IdentityDocumentRequiredException(caller.username)
        return application

    @staticmethod
    def fitting_for(caller: User, application: Application) -> list[int] | None:
        """The age categories a member fits, for their read (R11a); an
        admin's read carries none."""
        if is_admin(caller):
            return None
        member = member_document(caller)
        born = member["date_of_birth"]
        return fitting_categories(
            template_document(application)["age_categories"],
            date.fromisoformat(born) if born else None,
            member["gender"],
        )

    async def own_form(
        self, caller: User, application_id: int, category: int | None = None
    ) -> bytes:
        """The caller's own form of a published template, ticking
        ``category`` (R10, R11, R11a)."""
        application = await self.get_for(caller, application_id)
        return await self._form(application, caller, category)

    async def member_form(
        self, application_id: int, username: str, category: int | None = None
    ) -> bytes:
        """An admin's download of a member's form (R20)."""
        application = await self._applications.get_or_raise(application_id)
        member = await UserService(self.db).get_user_or_raise(username)
        return await self._form(application, member, category)

    async def preview(self, application_id: int) -> bytes:
        """The demo member's form, in any state, watermarked (R19)."""
        application = await self._applications.get_or_raise(application_id)
        template = template_document(application)
        stored = application.layout
        options = frozen_options(stored, await self._options())
        today = day_to_date(club_today())
        return draw(
            template,
            preview_member(template, options, today),
            options,
            await club_logo(self.db),
            today,
            stored,
            preview=True,
        )

    async def _options(self) -> JsonDict:
        return options_document(await ProfileOptionsService(self.db).read())

    async def _form(
        self, application: Application, member: User, category: int | None
    ) -> bytes:
        """A published template's form, drawn only from its stored layout and
        frozen options: never measured again, so it never fails for the
        template's sake (R14, R20); ``category`` is the member's choice (R11a)."""
        if application.status != PUBLISHED:
            raise ApplicationNotPublishedException(application.id)
        stored = application.layout
        return draw(
            template_document(application),
            {**member_document(member), "chosen_category": category},
            frozen_options(stored, await self._options()),
            await club_logo(self.db),
            day_to_date(club_today()),
            stored,
            publish_id=application.publish_id,
        )
