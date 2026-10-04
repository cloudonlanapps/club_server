"""Broadcast service — admin-authored fan-out messages (#51).

A broadcast is a thin layer on top of the per-recipient notifications
feed: one ``Broadcast`` row records the audience selector and the
payload; the actual delivery is one ``Notification`` row per resolved
recipient, linked back via ``notifications.broadcast_id``. This keeps
read tracking, pagination, and the user-facing feed unchanged.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.broadcast import Broadcast, BroadcastStatus
from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.group import Group, GroupMember
from ..db.models.notification import Notification
from ..db.models.user import User, UserStatus
from ..exceptions import (
    BroadcastNotFoundException,
    InvalidAudienceSelectorException,
)
from ..mailer import EmailMessage, get_broadcast_sender
from ..mailer import templates as mailer_templates
from .club_info import ClubInfoService
from .notification_recipients import deliverable_usernames
from ..schemas.common import UserRoles
from ..utils import ceil_to_utc_day, now_utc_ms


_BROADCAST_TYPE = "broadcast.message"


@dataclass(frozen=True)
class BroadcastEmailSummary:
    """Outcome of the optional email delivery for a broadcast.

    ``requested`` is False when the broadcast was app-only. Otherwise the counts
    partition the resolved recipients: ``sent`` delivered, ``skipped_no_email``
    had no address on file, ``failed`` had an address but the provider rejected
    the send.
    """

    requested: bool = False
    sent: int = 0
    skipped_no_email: int = 0
    failed: int = 0


# Enrollment statuses considered "currently a member" for event-audience selectors.
_ACTIVE_ENROLLMENT_STATUSES = {
    EnrollmentStatus.invited.value,
    EnrollmentStatus.requested.value,
    EnrollmentStatus.accepted.value,
    EnrollmentStatus.assigned.value,
    EnrollmentStatus.assigned_trial.value,
    EnrollmentStatus.withdraw_requested.value,
}


class BroadcastService:
    """Service for the admin-only broadcast surface."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    # ------------------------------------------------------------------
    # Audience resolution
    # ------------------------------------------------------------------

    async def resolve_audience(self, selector: dict[str, Any]) -> list[str]:
        """Resolve a v1 audience selector to a list of distinct active
        usernames. Raises ``InvalidAudienceSelectorException`` on
        malformed input."""
        kind = selector.get("kind")
        if not isinstance(kind, str):
            raise InvalidAudienceSelectorException("audienceSelector.kind is required")

        if kind == "all_users":
            return await self._all_active_users()
        if kind == "role":
            role = selector.get("role")
            if role not in {"admin", "coach"}:
                raise InvalidAudienceSelectorException(
                    "role audience requires role ∈ {admin, coach}"
                )
            return await self._users_with_role(role)
        if kind == "group":
            group_id = selector.get("groupId")
            if not isinstance(group_id, int):
                raise InvalidAudienceSelectorException(
                    "group audience requires integer groupId"
                )
            return await self._group_members(group_id)
        if kind == "event_members":
            event_id = selector.get("eventId")
            if not isinstance(event_id, int):
                raise InvalidAudienceSelectorException(
                    "event_members audience requires integer eventId"
                )
            return await self._event_members(event_id)
        if kind == "event_staff":
            event_id = selector.get("eventId")
            if not isinstance(event_id, int):
                raise InvalidAudienceSelectorException(
                    "event_staff audience requires integer eventId"
                )
            # v1 routes the event_staff audience to global admins+coaches.
            # The event's organizer and coaches are user references (#386),
            # so narrowing this to them is a separate change.
            return await self._staff_usernames()
        if kind == "users":
            usernames = selector.get("usernames")
            if not isinstance(usernames, list) or not all(
                isinstance(u, str) for u in usernames
            ):
                raise InvalidAudienceSelectorException(
                    "users audience requires a list of strings"
                )
            return await self._filter_existing_active_users(usernames)
        raise InvalidAudienceSelectorException(f"unsupported audience kind: {kind}")

    async def _all_active_users(self) -> list[str]:
        result = await self.db.execute(
            select(User.username).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        return [u for (u,) in result.all()]

    async def _users_with_role(self, role: str) -> list[str]:
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        usernames: list[str] = []
        for u in result.scalars().all():
            if u.is_super_admin and role == "admin":
                usernames.append(u.username)
                continue
            if not u.roles:
                continue
            parsed = UserRoles.model_validate_json(u.roles)
            if role in parsed.roles:
                usernames.append(u.username)
        return usernames

    async def _staff_usernames(self) -> list[str]:
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        usernames: list[str] = []
        for u in result.scalars().all():
            if u.is_super_admin:
                usernames.append(u.username)
                continue
            if not u.roles:
                continue
            parsed = UserRoles.model_validate_json(u.roles)
            roles = set(parsed.roles)
            if "admin" in roles or "coach" in roles:
                usernames.append(u.username)
        return usernames

    async def _group_members(self, group_id: int) -> list[str]:
        # Verify group exists. We don't expand auto-group criteria here:
        # broadcasts target explicit members for manual/semi_auto groups,
        # consistent with how #47 handles group.archived/settings_changed.
        group_result = await self.db.execute(
            select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
        )
        if group_result.scalar_one_or_none() is None:
            raise InvalidAudienceSelectorException(
                f"group {group_id} not found or deleted"
            )
        result = await self.db.execute(
            select(GroupMember.membername).where(GroupMember.group_id == group_id)
        )
        return [u for (u,) in result.all()]

    async def _event_members(self, event_id: int) -> list[str]:
        result = await self.db.execute(
            select(Enrollment.membername).where(
                Enrollment.event_id == event_id,
                Enrollment.status.in_(_ACTIVE_ENROLLMENT_STATUSES),
            )
        )
        return [u for (u,) in result.all()]

    async def _filter_existing_active_users(self, usernames: list[str]) -> list[str]:
        if not usernames:
            return []
        result = await self.db.execute(
            select(User.username).where(
                User.username.in_(usernames),
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        return [u for (u,) in result.all()]

    # ------------------------------------------------------------------
    # Create / list / detail / revoke
    # ------------------------------------------------------------------

    async def create_broadcast(
        self,
        sender_username: str,
        audience_selector: dict[str, Any],
        payload: dict[str, Any],
        expires_at: int | None = None,
        email: bool = False,
        email_subject: str | None = None,
        email_body: str | None = None,
    ) -> tuple[Broadcast, int, BroadcastEmailSummary]:
        """Create a broadcast and fan it out synchronously.

        Returns ``(broadcast, recipient_count, email_summary)``. Unknown /
        inactive usernames in the resolved audience, and users whose account
        state does not receive a broadcast, are dropped silently. When
        ``email`` is true the same audience is additionally emailed via the
        broadcast provider; email delivery is best-effort and independent of the
        in-app fan-out (a send failure never affects the notification rows)."""
        resolved = list(dict.fromkeys(await self.resolve_audience(audience_selector)))
        deliverable = await deliverable_usernames(self.db, resolved, _BROADCAST_TYPE)
        recipients = [u for u in resolved if u in deliverable]
        now = now_utc_ms()
        normalized_expires_at = (
            ceil_to_utc_day(expires_at) if expires_at is not None else None
        )

        broadcast = Broadcast(
            sender_username=sender_username,
            audience_selector=audience_selector,
            payload=payload,
            sent_at=now,
            expires_at=normalized_expires_at,
            status=BroadcastStatus.sent.value,
        )
        self.db.add(broadcast)
        await self.db.flush()

        for username in recipients:
            row = Notification(
                username=username,
                type=_BROADCAST_TYPE,
                channel="app",
                payload=payload,
                broadcast_id=broadcast.id,
                is_read=0,
                created_at=now,
            )
            self.db.add(row)
        if recipients:
            await self.db.flush()

        email_summary = BroadcastEmailSummary()
        if email:
            email_summary = await self._deliver_broadcast_emails(
                recipients, email_subject or "", email_body or ""
            )

        return broadcast, len(recipients), email_summary

    async def _deliver_broadcast_emails(
        self, recipients: list[str], email_subject: str, email_body: str
    ) -> BroadcastEmailSummary:
        """Email the broadcast to recipients who have an address on file.

        Explicit per-broadcast delivery — deliberately does NOT consult
        per-user notification preferences (an admin who set email=true is
        overriding them). Recipients without an email are skipped, not errored.
        """
        emails = await self._emails_for(recipients)
        club_name, club_short_name = await ClubInfoService(self.db).branding()
        subject, html, text = mailer_templates.broadcast(
            club_name=club_name,
            club_short_name=club_short_name,
            email_subject=email_subject,
            email_body=email_body,
        )
        sender = get_broadcast_sender()
        sent = failed = 0
        for username in recipients:
            address = emails.get(username)
            if not address:
                continue
            result = await sender.send(
                EmailMessage(to=address, subject=subject, html=html, text=text)
            )
            if result.success:
                sent += 1
            else:
                failed += 1
        return BroadcastEmailSummary(
            requested=True,
            sent=sent,
            skipped_no_email=len(recipients) - sent - failed,
            failed=failed,
        )

    async def _emails_for(self, usernames: list[str]) -> dict[str, str]:
        """Map usernames to their email address, omitting users without one."""
        if not usernames:
            return {}
        rows = await self.db.execute(
            select(User.username, User.email).where(
                User.username.in_(usernames),
                User.email.is_not(None),
                User.deleted_at.is_(None),
            )
        )
        return {username: email for username, email in rows.all()}

    async def list_broadcasts(
        self, offset: int = 0, limit: int = 20
    ) -> list[tuple[Broadcast, int]]:
        """List broadcasts (newest first) with current recipient count."""
        count_subq = (
            select(Notification.broadcast_id, func.count().label("c"))
            .where(Notification.broadcast_id.is_not(None))
            .group_by(Notification.broadcast_id)
            .subquery()
        )
        query = (
            select(Broadcast, func.coalesce(count_subq.c.c, 0))
            .outerjoin(count_subq, count_subq.c.broadcast_id == Broadcast.id)
            .order_by(Broadcast.sent_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(query)
        return [(b, int(c)) for (b, c) in result.all()]

    async def count_broadcasts(self) -> int:
        result = await self.db.execute(select(func.count()).select_from(Broadcast))
        return int(result.scalar_one())

    async def get_broadcast_detail(
        self, broadcast_id: int
    ) -> tuple[Broadcast, int, int]:
        """Returns ``(broadcast, read_count, unread_count)``."""
        broadcast = await self._get_or_raise(broadcast_id)
        agg = await self.db.execute(
            select(
                func.coalesce(
                    func.sum(case((Notification.is_read == 1, 1), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((Notification.is_read == 0, 1), else_=0)), 0
                ),
            ).where(Notification.broadcast_id == broadcast_id)
        )
        read_count, unread_count = agg.one()
        return broadcast, int(read_count), int(unread_count)

    async def list_recipients(
        self,
        broadcast_id: int,
        status_filter: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[str, bool, int]], int]:
        """Returns ``(rows, total)`` for the recipients tab."""
        _ = await self._get_or_raise(broadcast_id)

        base = select(Notification).where(Notification.broadcast_id == broadcast_id)
        if status_filter == "read":
            base = base.where(Notification.is_read == 1)
        elif status_filter == "unread":
            base = base.where(Notification.is_read == 0)
        elif status_filter is not None:
            raise InvalidAudienceSelectorException(
                "status must be 'read' or 'unread' if provided"
            )

        total_result = await self.db.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = int(total_result.scalar_one())

        rows_result = await self.db.execute(
            base.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        )
        rows = [
            (n.username, bool(n.is_read), n.created_at)
            for n in rows_result.scalars().all()
        ]
        return rows, total

    async def revoke_broadcast(self, broadcast_id: int) -> Broadcast:
        """Delete the fan-out rows; keep the broadcast row for audit."""
        broadcast = await self._get_or_raise(broadcast_id)
        _ = await self.db.execute(
            delete(Notification).where(Notification.broadcast_id == broadcast_id)
        )
        broadcast.status = BroadcastStatus.revoked.value
        await self.db.flush()
        return broadcast

    async def _get_or_raise(self, broadcast_id: int) -> Broadcast:
        result = await self.db.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )
        broadcast = result.scalar_one_or_none()
        if broadcast is None:
            raise BroadcastNotFoundException(broadcast_id)
        return broadcast
