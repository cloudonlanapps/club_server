"""Notification service for notification management."""

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import Select, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.attendance import AttendanceRecord, AttendanceStatus
from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.group_join_request import GroupJoinRequest, JoinRequestStatus
from ..db.models.notification import Notification, NotificationPref
from ..exceptions import (
    NotificationNotFoundException,
    RecipientNotDeliverableException,
    UserNotFoundException,
)
from ..db.models.user import User, UserStatus
from ..schemas.common import PaginatedResponse
from ..schemas.notification import NotificationPrefResponse, NotificationResponse
from ..utils import now_utc_ms
from .notification_recipients import deliverable_usernames


PAYLOAD_VERSION = 1


# Grace window (ms) for the undo-cancel notification policy: a cancellation
# notification still unread and newer than this is deleted on undo so the user
# never sees the cancel/restore churn; older or already-read ones are kept and
# a restored notification is fired instead.
UNDO_GRACE_WINDOW_MS = 5 * 60 * 1000


PendingActionJoin = Callable[[Select[Any]], Select[Any]]
"""Apply a join + unresolved-state filter to a ``select(Notification)`` query."""


def _join_group_join_request(query: Select[Any]) -> Select[Any]:
    return query.join(
        GroupJoinRequest,
        and_(
            Notification.pending_action_type == "group_join_request",
            Notification.pending_action_id == GroupJoinRequest.id,
            GroupJoinRequest.status == JoinRequestStatus.pending.value,
        ),
    )


def _join_enrollment_opportunity(query: Select[Any]) -> Select[Any]:
    return query.join(
        Enrollment,
        and_(
            Notification.pending_action_type == "enrollment_opportunity",
            Notification.pending_action_id == Enrollment.id,
            Enrollment.status == EnrollmentStatus.invited.value,
        ),
    )


def _join_enrollment_request(query: Select[Any]) -> Select[Any]:
    return query.join(
        Enrollment,
        and_(
            Notification.pending_action_type == "enrollment_request",
            Notification.pending_action_id == Enrollment.id,
            Enrollment.status == EnrollmentStatus.requested.value,
        ),
    )


def _join_attendance_correction(query: Select[Any]) -> Select[Any]:
    return query.join(
        AttendanceRecord,
        and_(
            Notification.pending_action_type == "attendance_correction",
            Notification.pending_action_id == AttendanceRecord.id,
            AttendanceRecord.status == AttendanceStatus.on_leave_requested.value,
        ),
    )


def _join_user_approval(query: Select[Any]) -> Select[Any]:
    return query.join(
        User,
        and_(
            Notification.pending_action_type == "user_approval",
            Notification.pending_action_key == User.username,
            User.status == UserStatus.pending.value,
            User.deleted_at.is_(None),
        ),
    )


PENDING_ACTION_JOINS: dict[str, PendingActionJoin] = {
    "group_join_request": _join_group_join_request,
    "enrollment_opportunity": _join_enrollment_opportunity,
    "enrollment_request": _join_enrollment_request,
    "attendance_correction": _join_attendance_correction,
    "user_approval": _join_user_approval,
}
"""Registry mapping ``pending_action_type`` to a join+filter on the source
table's unresolved state. Domain PRs register their own types here as they
land (e.g. ``group_invitation`` in #47, ``enrollment_opportunity`` in #48).
"""


@dataclass
class NotificationEvent:
    """Domain event handed to ``NotificationService.notify_for_event``.

    ``type`` is the dotted event identifier (e.g. ``group.invite``).
    ``recipients`` is the list of usernames that should receive a row.
    ``data`` is the fact payload (no prose).
    ``pending_action_type`` / ``pending_action_id`` link the notification to
    an existing domain row whose unresolved state gates ``pending-actions``.
    """

    type: str
    recipients: list[str]
    data: dict[str, Any] = field(default_factory=dict)
    channel: str = "app"
    pending_action_type: str | None = None
    pending_action_id: int | None = None
    pending_action_key: str | None = None


class NotificationService:
    """Service for notification management."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def list_notifications(
        self,
        username: str,
        offset: int = 0,
        limit: int = 20,
        unread_only: bool = False,
    ) -> PaginatedResponse[NotificationResponse]:
        """List notifications for a user."""
        query = select(Notification).where(Notification.username == username)

        if unread_only:
            query = query.where(Notification.is_read == 0)

        count_query = select(Notification.id).where(Notification.username == username)
        if unread_only:
            count_query = count_query.where(Notification.is_read == 0)
        count_result = await self.db.execute(count_query)
        total = len(count_result.scalars().all())

        query = (
            query.offset(offset).limit(limit).order_by(Notification.created_at.desc())
        )
        result = await self.db.execute(query)
        notifications = result.scalars().all()

        return PaginatedResponse(
            items=[NotificationResponse.from_model(n) for n in notifications],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_pending_actions(
        self,
        username: str,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[NotificationResponse]:
        """List the user's actionable notifications whose linked source row
        is still unresolved.

        A notification appears only when:
        - ``pending_action_type`` is registered in ``PENDING_ACTION_JOINS``
          (so we know how to check resolution), and
        - the joined source row matches its registered "unresolved" filter.

        Notifications whose type is registered but whose source row has
        resolved are filtered out — that is the "auto-dismiss" behaviour.
        """
        items: list[Notification] = []
        total = 0
        per_type_queries: list[Select[Any]] = []

        for action_type, join in PENDING_ACTION_JOINS.items():
            base = select(Notification).where(
                Notification.username == username,
                Notification.pending_action_type == action_type,
            )
            per_type_queries.append(join(base))

        for q in per_type_queries:
            count_result = await self.db.execute(q)
            rows = list(count_result.scalars().all())
            total += len(rows)
            items.extend(rows)

        items.sort(key=lambda n: n.created_at, reverse=True)
        page = items[offset : offset + limit]

        return PaginatedResponse(
            items=[NotificationResponse.from_model(n) for n in page],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def get_unread_count(self, username: str) -> int:
        """Get count of unread notifications."""
        result = await self.db.execute(
            select(Notification.id).where(
                Notification.username == username,
                Notification.is_read == 0,
            )
        )
        return len(result.scalars().all())

    async def mark_read(self, notification_id: int, username: str) -> None:
        """Mark a notification as read."""
        result = await self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.username == username,
            )
        )
        notification = result.scalar_one_or_none()

        if not notification:
            raise NotificationNotFoundException(notification_id)

        if notification.is_read == 0:
            notification.is_read = 1
            await self.db.flush()

    async def mark_all_read(self, username: str) -> None:
        """Mark all notifications as read."""
        result = await self.db.execute(
            select(Notification).where(
                Notification.username == username,
                Notification.is_read == 0,
            )
        )
        notifications = result.scalars().all()

        for notification in notifications:
            notification.is_read = 1

        await self.db.flush()

    async def get_preferences(self, username: str) -> NotificationPrefResponse:
        """Get notification preferences for a user."""
        result = await self.db.execute(
            select(NotificationPref).where(NotificationPref.username == username)
        )
        pref = result.scalar_one_or_none()

        if pref:
            return NotificationPrefResponse(
                email_enabled=bool(pref.email_enabled),
                push_enabled=bool(pref.push_enabled),
                sms_enabled=bool(pref.sms_enabled),
            )

        return NotificationPrefResponse(
            email_enabled=True,
            push_enabled=True,
            sms_enabled=False,
        )

    async def update_preferences(
        self,
        username: str,
        email_enabled: bool | None = None,
        push_enabled: bool | None = None,
        sms_enabled: bool | None = None,
    ) -> NotificationPrefResponse:
        """Update notification preferences."""
        result = await self.db.execute(
            select(NotificationPref).where(NotificationPref.username == username)
        )
        pref = result.scalar_one_or_none()

        if pref:
            if email_enabled is not None:
                pref.email_enabled = 1 if email_enabled else 0
            if push_enabled is not None:
                pref.push_enabled = 1 if push_enabled else 0
            if sms_enabled is not None:
                pref.sms_enabled = 1 if sms_enabled else 0
        else:
            pref = NotificationPref(
                username=username,
                email_enabled=1
                if email_enabled
                else (0 if email_enabled is not None else 1),
                push_enabled=1
                if push_enabled
                else (0 if push_enabled is not None else 1),
                sms_enabled=1 if sms_enabled else 0,
            )
            self.db.add(pref)

        await self.db.flush()

        return NotificationPrefResponse(
            email_enabled=bool(pref.email_enabled),
            push_enabled=bool(pref.push_enabled),
            sms_enabled=bool(pref.sms_enabled),
        )

    async def create_notification(
        self,
        username: str,
        notification_type: str,
        channel: str,
        payload: dict[str, Any],
        pending_action_type: str | None = None,
        pending_action_id: int | None = None,
        pending_action_key: str | None = None,
        broadcast_id: int | None = None,
    ) -> Notification:
        """Create a notification row for a user.

        Raises ``UserNotFoundException`` for an unknown or soft-deleted user
        and ``RecipientNotDeliverableException`` for one whose account state
        does not receive this type (notifications R7a).
        """
        user_result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)
        if not await deliverable_usernames(self.db, [username], notification_type):
            raise RecipientNotDeliverableException(username, notification_type)

        now = now_utc_ms()
        notification = Notification(
            username=username,
            type=notification_type,
            channel=channel,
            payload=payload,
            pending_action_type=pending_action_type,
            pending_action_id=pending_action_id,
            pending_action_key=pending_action_key,
            broadcast_id=broadcast_id,
            is_read=0,
            created_at=now,
        )
        self.db.add(notification)
        await self.db.flush()

        return notification

    async def notify_for_event(self, event: NotificationEvent) -> list[Notification]:
        """Central entry point for domain-driven notification generation.

        Wraps the event in the ``{v, type, data}`` payload contract and
        inserts one row per recipient. Unknown recipients, and those whose
        account state does not receive this type, are skipped silently.

        Per-domain handlers (group/enrollment/attendance/event) are wired
        through this single call site so the coupling between domain logic
        and the notification module stays narrow.
        """
        if not event.recipients:
            return []

        payload = {"v": PAYLOAD_VERSION, "type": event.type, "data": event.data}
        now = now_utc_ms()
        created: list[Notification] = []

        valid_recipients = await deliverable_usernames(
            self.db, event.recipients, event.type
        )

        for username in event.recipients:
            if username not in valid_recipients:
                continue
            notification = Notification(
                username=username,
                type=event.type,
                channel=event.channel,
                payload=payload,
                pending_action_type=event.pending_action_type,
                pending_action_id=event.pending_action_id,
                pending_action_key=event.pending_action_key,
                is_read=0,
                created_at=now,
            )
            self.db.add(notification)
            created.append(notification)

        if created:
            await self.db.flush()
        return created

    async def apply_undo_notification_policy(
        self,
        *,
        audience: list[str],
        cancelled_type: str,
        restored_type: str,
        match: dict[str, Any],
        restored_data: dict[str, Any],
    ) -> None:
        """Per-recipient hybrid policy for undoing a cancellation.

        For each user in ``audience``, find their most recent ``cancelled_type``
        notification whose payload ``data`` matches ``match``. If it is unread
        and within ``UNDO_GRACE_WINDOW_MS``, delete it and fire nothing (the
        user never sees the cancel/restore churn). Otherwise leave it and fire a
        ``restored_type`` notification so they see the cancel→restore sequence.
        """
        now = now_utc_ms()
        fire_to: list[str] = []

        for username in audience:
            result = await self.db.execute(
                select(Notification)
                .where(
                    Notification.username == username,
                    Notification.type == cancelled_type,
                )
                .order_by(Notification.created_at.desc())
            )
            cancelled = None
            for candidate in result.scalars().all():
                data = (candidate.payload or {}).get("data", {})
                if all(data.get(k) == v for k, v in match.items()):
                    cancelled = candidate
                    break

            if (
                cancelled is not None
                and cancelled.is_read == 0
                and now - cancelled.created_at <= UNDO_GRACE_WINDOW_MS
            ):
                await self.db.delete(cancelled)
            else:
                fire_to.append(username)

        await self.db.flush()

        if fire_to:
            await self.notify_for_event(
                NotificationEvent(
                    type=restored_type,
                    recipients=fire_to,
                    data=restored_data,
                )
            )

    async def clear_actionable(
        self,
        pending_action_type: str,
        pending_action_id: int | None = None,
        pending_action_key: str | None = None,
    ) -> int:
        """Delete every actionable notification linked to a resolved source row.

        Resolution paths call this after a pending action reaches a terminal
        state so the matching notification rows are removed from both
        ``GET /v1/notifications`` and ``GET /v1/notifications/pending-actions``.

        At least one of ``pending_action_id`` / ``pending_action_key`` must be
        supplied; both are accepted to match types that key by either id or
        username. Returns the row count deleted.
        """
        if pending_action_id is None and pending_action_key is None:
            raise ValueError(
                "clear_actionable requires pending_action_id or pending_action_key"
            )

        match_clauses = []
        if pending_action_id is not None:
            match_clauses.append(Notification.pending_action_id == pending_action_id)
        if pending_action_key is not None:
            match_clauses.append(Notification.pending_action_key == pending_action_key)

        stmt = delete(Notification).where(
            Notification.pending_action_type == pending_action_type,
            or_(*match_clauses),
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount or 0

    async def clear_actionable_by_key(self, pending_action_key: str) -> int:
        """Delete every actionable notification whose ``pending_action_key``
        matches ``pending_action_key``, regardless of ``pending_action_type``.

        Used when the subject of a pending action is removed from the system
        (e.g. hard-deleted user) and the action can therefore never resolve.
        Returns the row count deleted.
        """
        stmt = delete(Notification).where(
            Notification.pending_action_key == pending_action_key,
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount or 0

    async def delete_notification(
        self, notification_id: int, username: str | None = None
    ) -> None:
        """Delete a notification. If username provided, verify ownership."""
        query = select(Notification).where(Notification.id == notification_id)
        if username:
            query = query.where(Notification.username == username)

        result = await self.db.execute(query)
        notification = result.scalar_one_or_none()

        if not notification:
            raise NotificationNotFoundException(notification_id)

        await self.db.delete(notification)
        await self.db.flush()
