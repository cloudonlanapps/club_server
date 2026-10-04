"""Inquiries: accept, deliver, curate and purge (#407, public R23–R30).

Spam defence lives here, not in a follow-up: a forged or expired token is
refused loudly (a broken form), while a honeypot hit or a duplicate is
accepted and discarded (a bot should not learn it was caught).
"""

import base64
import hashlib
import hmac
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.inquiry import Inquiry
from ..db.models.user import User, UserStatus
from ..exceptions import InquiryNotFoundException, InvalidFormTokenException
from ..mailer import templates as mailer_templates
from ..mailer.factory import get_transactional_sender
from ..mailer.sender import EmailMessage
from ..schemas.common import PaginatedResponse, UserRoles
from ..schemas.inquiry import InquiryResponse, InquirySubmission
from ..utils import now_utc_ms
from .club_info import ClubInfoService
from .notification import NotificationEvent, NotificationService

MIN_FILL_MS = 3_000
TOKEN_TTL_MS = 60 * 60 * 1000
DEDUPE_WINDOW_MS = 10 * 60 * 1000
HANDLED_RETENTION_DAYS = 180
UNHANDLED_RETENTION_DAYS = 365
_MS_PER_DAY = 86_400_000
NOTIFICATION_TYPE = "inquiry.received"


def _sign(issued_at_ms: int) -> str:
    key = settings.secret_key.encode("utf-8")
    digest = hmac.new(key, str(issued_at_ms).encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")[:32]


def issue_form_token(*, issued_at_ms: int | None = None) -> str:
    """``<issued_at_ms>.<signature>``; the client sends it back unchanged (R24)."""
    issued = issued_at_ms if issued_at_ms is not None else now_utc_ms()
    return f"{issued}.{_sign(issued)}"


def verify_form_token(token: str, now_ms: int) -> int:
    """The token's issue time. Forged, malformed or expired → InvalidFormTokenException."""
    try:
        raw_issued, signature = token.split(".", 1)
        issued = int(raw_issued)
    except ValueError as exc:
        raise InvalidFormTokenException() from exc
    if not hmac.compare_digest(signature, _sign(issued)):
        raise InvalidFormTokenException()
    if issued > now_ms or now_ms - issued > TOKEN_TTL_MS:
        raise InvalidFormTokenException()
    return issued


def source_hash(client_ip: str | None) -> str:
    """Salted hash of the client address; the address itself is never stored."""
    key = settings.secret_key.encode("utf-8")
    return hmac.new(key, (client_ip or "").encode("utf-8"), hashlib.sha256).hexdigest()


class InquiryService:
    """Accepts submissions and serves the admin inbox."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    # --- public write ------------------------------------------------------

    async def submit(self, data: InquirySubmission, client_ip: str | None) -> None:
        """Store, email and notify; or silently discard a bot or a duplicate (R23–R25, R27)."""
        now = now_utc_ms()
        issued = verify_form_token(data.token, now)
        if data.website:
            return  # honeypot: a human never fills it
        if now - issued < MIN_FILL_MS:
            return  # filled faster than a person could
        source = source_hash(client_ip)
        if await self._recent_duplicate(source, data.email, now):
            return

        row = Inquiry(
            kind=data.kind,
            name=data.name,
            email=str(data.email),
            phone=data.phone,
            message=data.message,
            extra=data.extra,
            source_hash=source,
            created_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        await self._deliver(row)

    async def _recent_duplicate(self, source: str, email: str, now: int) -> bool:
        result = await self.db.execute(
            select(func.count())
            .select_from(Inquiry)
            .where(
                Inquiry.source_hash == source,
                Inquiry.email == email,
                Inquiry.created_at >= now - DEDUPE_WINDOW_MS,
            )
        )
        return result.scalar_one() > 0

    async def _admin_usernames(self) -> list[str]:
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None), User.status == UserStatus.active.value
            )
        )
        admins: list[str] = []
        for user in result.scalars().all():
            roles = (
                UserRoles.model_validate_json(user.roles).roles if user.roles else []
            )
            if user.is_super_admin or "admin" in roles:
                admins.append(user.username)
        return admins

    async def _deliver(self, row: Inquiry) -> None:
        club_info = ClubInfoService(self.db)
        club_name, club_short_name = await club_info.branding()
        address = (await club_info.public_document()).club_info.get("inquiryEmail")
        if isinstance(address, str) and address:
            subject, html, text = mailer_templates.inquiry_received(
                club_name=club_name,
                club_short_name=club_short_name,
                kind=row.kind,
                name=row.name,
                email=row.email,
                phone=row.phone,
                message=row.message,
                extra=row.extra,
            )
            await get_transactional_sender().send(
                EmailMessage(to=address, subject=subject, html=html, text=text)
            )
        await NotificationService(self.db).notify_for_event(
            NotificationEvent(
                type=NOTIFICATION_TYPE,
                recipients=await self._admin_usernames(),
                data={"inquiryId": row.id, "kind": row.kind, "name": row.name},
            )
        )

    # --- admin inbox -------------------------------------------------------

    async def list_inquiries(
        self, *, kind: str | None, handled: bool | None, offset: int, limit: int
    ) -> PaginatedResponse[InquiryResponse]:
        """Newest first, filtered by kind and handled state (R28)."""
        query = select(Inquiry)
        if kind is not None:
            query = query.where(Inquiry.kind == kind)
        if handled is True:
            query = query.where(Inquiry.handled_at.isnot(None))
        elif handled is False:
            query = query.where(Inquiry.handled_at.is_(None))
        total = (
            await self.db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            (
                await self.db.execute(
                    query.order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return PaginatedResponse(
            items=[InquiryResponse.from_model(r) for r in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def _get(self, inquiry_id: int) -> Inquiry:
        row = await self.db.get(Inquiry, inquiry_id)
        if row is None:
            raise InquiryNotFoundException(inquiry_id)
        return row

    async def set_handled(
        self, inquiry_id: int, handled: bool, actor: str
    ) -> InquiryResponse:
        """Mark handled by ``actor`` now, or reopen (R28)."""
        row = await self._get(inquiry_id)
        row.handled_at = now_utc_ms() if handled else None
        row.handled_by = actor if handled else None
        await self.db.flush()
        return InquiryResponse.from_model(row)

    async def delete(self, inquiry_id: int) -> None:
        """Hard delete: it is PII (R28)."""
        row = await self._get(inquiry_id)
        await self.db.delete(row)
        await self.db.flush()


async def purge_expired_inquiries(session: AsyncSession, now: int) -> int:
    """Daily sweep: handled after 180 days, unhandled after 365 (R29)."""
    result = await session.execute(
        delete(Inquiry).where(
            or_(
                Inquiry.handled_at <= now - HANDLED_RETENTION_DAYS * _MS_PER_DAY,
                (Inquiry.handled_at.is_(None))
                & (Inquiry.created_at <= now - UNHANDLED_RETENTION_DAYS * _MS_PER_DAY),
            )
        )
    )
    return result.rowcount or 0


def kind_label(kind: str) -> str:
    """Human wording for a kind, for mail subjects."""
    return "contact request" if kind == "contact" else "interest registration"


__all__ = [
    "InquiryService",
    "issue_form_token",
    "verify_form_token",
    "purge_expired_inquiries",
    "kind_label",
    "source_hash",
]
