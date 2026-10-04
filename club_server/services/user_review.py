"""Service for the user reconsider / reapply workflow (#122).

The workflow lives next to ``UserService`` rather than inside it: it
owns its own table, has tight transactional semantics (status flip,
notification cleanup, audit hook) and a small surface area. Keeping
it separate avoids further growth of ``UserService``.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.notification import Notification
from ..db.models.user import User, UserStatus
from ..db.models.user_review_request import UserReviewRequest
from ..exceptions import InvalidStateException, UserNotFoundException
from ..utils import now_utc_ms
from ..validation import validate_utc_midnight


RESOLUTION_RESUBMITTED = "resubmitted"
RESOLUTION_APPROVED = "approved"
RESOLUTION_BLOCKED = "blocked"
RESOLUTION_SUPERSEDED = "superseded"


class CannotReconsiderSelfException(Exception):
    """Raised when an admin tries to reconsider themselves."""

    def __init__(self) -> None:
        super().__init__("Cannot reconsider self")


class NoActiveReviewRequestException(Exception):
    """Raised when reapply is attempted with no active review request."""

    def __init__(self) -> None:
        super().__init__("No active review request for user")


class UserReviewService:
    """Service backing ``reconsider`` / ``reapply`` and the closure
    side-effects of ``approve`` / ``block``."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def get_active_review_note(self, user: User) -> str | None:
        """Return the active review request's ``reason`` for ``user``,
        or ``None`` if there is no active row (see #123).

        Short-circuits without a DB query when ``user.status`` is not
        ``registered`` — by invariant, an active row can only exist
        while the user sits in that status.
        """
        if user.status != UserStatus.registered.value:
            return None
        active = await self._get_active_request(user.username)
        return active.reason if active is not None else None

    async def _get_active_request(self, username: str) -> UserReviewRequest | None:
        result = await self.db.execute(
            select(UserReviewRequest).where(
                UserReviewRequest.username == username,
                UserReviewRequest.resolved_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _get_user_or_raise(self, username: str) -> User:
        result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)
        return user

    async def reconsider(
        self,
        target_username: str,
        actor_username: str,
        reason: str,
    ) -> UserReviewRequest:
        """Ask a pending user to revisit registration.

        Atomically supersedes any active row, inserts a fresh active
        row, flips target ``pending → registered``, and deletes any
        outstanding ``user_approval`` notifications so admins don't
        see two copies after the user resubmits.
        """
        if target_username == actor_username:
            raise CannotReconsiderSelfException()

        target = await self._get_user_or_raise(target_username)

        if target.status != UserStatus.pending.value:
            raise InvalidStateException(
                f"User is not pending (current status: {target.status})"
            )

        now = now_utc_ms()

        active = await self._get_active_request(target_username)
        if active is not None:
            active.resolved_at = now
            active.resolved_by = actor_username
            active.resolution = RESOLUTION_SUPERSEDED

        new_row = UserReviewRequest(
            username=target_username,
            reason=reason,
            requested_by=actor_username,
            created_at=now,
        )
        self.db.add(new_row)

        target.status = UserStatus.registered.value

        _ = await self.db.execute(
            delete(Notification).where(
                Notification.pending_action_type == "user_approval",
                Notification.pending_action_key == target_username,
            )
        )

        await self.db.flush()

        return new_row

    async def reapply(
        self,
        username: str,
        update_fields: dict[str, object],
    ) -> tuple[User, UserReviewRequest]:
        """Apply the user's resubmission. Caller has already been
        authorised against ``username``. Status stays ``registered``;
        the caller transitions back to ``pending`` via
        ``POST /v1/users/me/submit-for-review`` (#120).
        """
        user = await self._get_user_or_raise(username)

        if user.status != UserStatus.registered.value:
            raise InvalidStateException(
                f"User is not registered (current status: {user.status})"
            )

        active = await self._get_active_request(username)
        if active is None:
            raise NoActiveReviewRequestException()

        dob = update_fields.get("date_of_birth")
        validate_utc_midnight(dob if isinstance(dob, int) else None, "dateOfBirthUtc")

        for field, value in update_fields.items():
            setattr(user, field, value)

        now = now_utc_ms()
        active.resolved_at = now
        active.resolved_by = username
        active.resolution = RESOLUTION_RESUBMITTED

        await self.db.flush()
        return user, active

    async def close_active(
        self,
        username: str,
        actor_username: str,
        resolution: str,
        resolution_reason: str | None = None,
    ) -> UserReviewRequest | None:
        """Close the active review row, if any, with the given
        ``resolution``. Used by ``approve_user`` / ``block_user`` as a
        same-transaction side-effect. Returns the closed row or
        ``None`` if there was nothing active.
        """
        active = await self._get_active_request(username)
        if active is None:
            return None

        active.resolved_at = now_utc_ms()
        active.resolved_by = actor_username
        active.resolution = resolution
        active.resolution_reason = resolution_reason
        await self.db.flush()
        return active
