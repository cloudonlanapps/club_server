"""User service for user management operations."""

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.evaluation_template import EvaluationTemplate
from ..db.models.event_schedule import EventSchedule
from ..db.models.media import Media
from ..db.models.occurrence_override import OccurrenceOverride
from ..db.models.media_links import UserMediaLink
from ..db.models.user import Role, User, UserStatus
from ..exceptions import (
    CannotBlockSuperAdminException,
    CannotTransferFromNonSuperAdminException,
    CannotTransferToSelfException,
    HardDeleteNeedsSoftDeleteException,
    IdentityDocumentRequiredException,
    InvalidStateException,
    NothingToRestoreException,
    RoleAlreadyAssignedException,
    RoleNotFoundException,
    UserAlreadyDeletedException,
    UserNotActiveException,
    UserNotFoundException,
)
from ..schemas.common import PaginatedResponse, UserRoles
from ..schemas.user import UserInfoResponse
from ..utils import now_utc_ms
from ..validation import validate_utc_midnight
from .auth import AuthService
from .notification import NotificationEvent, NotificationService

IDENTITY_DOCUMENT_TAG = "identity_document"


class _Unset:
    """Sentinel value to distinguish 'not provided' from 'explicitly None'."""

    pass


UNSET: Any = _Unset()


class UserService:
    """Service for user management operations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)

    async def _list_admin_usernames(self) -> list[str]:
        """Recipients for admin-facing user-domain events."""
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        admins: list[str] = []
        for u in result.scalars().all():
            if u.is_super_admin:
                admins.append(u.username)
                continue
            if not u.roles:
                continue
            parsed = UserRoles.model_validate_json(u.roles)
            if Role.admin.value in parsed.roles:
                admins.append(u.username)
        return admins

    def _roles_list(self, user: User) -> list[str]:
        if not user.roles:
            return []
        return UserRoles.model_validate_json(user.roles).roles

    async def get_user_or_raise(self, username: str) -> User:
        """Get user by username or raise exception."""
        result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)
        return user

    async def list_users(
        self,
        status_filter: UserStatus | None = None,
        role: str | None = None,
        min_age: int | None = None,
        max_age: int | None = None,
        search_term: str | None = None,
        sort_by: str | None = None,
        descending: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[UserInfoResponse]:
        """List users with optional filters."""
        query = select(User).where(User.deleted_at.is_(None), User.is_super_admin == 0)

        if status_filter is not None:
            query = query.filter(User.status == status_filter.value)
        else:
            query = query.filter(User.status != UserStatus.registered.value)

        if role:
            query = query.filter(User.roles.contains(f'"{role}"'))

        if min_age is not None or max_age is not None:
            ref_time = now_utc_ms()
            ms_per_year = 365.25 * 24 * 60 * 60 * 1000
            if min_age is not None:
                max_dob = ref_time - int(min_age * ms_per_year)
                query = query.filter(User.date_of_birth <= max_dob)
            if max_age is not None:
                min_dob = ref_time - int((max_age + 1) * ms_per_year)
                query = query.filter(User.date_of_birth > min_dob)

        if search_term:
            search_pattern = f"%{search_term}%"
            query = query.filter(
                or_(
                    User.username.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                    User.nickname.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                )
            )

        count_query = query.with_only_columns(func.count())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        sort_column = User.created_at
        if sort_by == "username":
            sort_column = User.username
        elif sort_by == "firstName":
            sort_column = User.first_name
        elif sort_by == "lastName":
            sort_column = User.last_name

        if descending:
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        query = query.offset(offset).limit(limit)
        result = await self.db.execute(query)
        users = result.scalars().all()

        return PaginatedResponse(
            items=[UserInfoResponse.from_model(u) for u in users],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_deleted_users(
        self,
        search_term: str | None = None,
        sort_by: str | None = None,
        descending: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[UserInfoResponse]:
        """List soft-deleted users."""
        query = select(User).where(
            User.deleted_at.isnot(None), User.is_super_admin == 0
        )

        if search_term:
            search_pattern = f"%{search_term}%"
            query = query.filter(
                or_(
                    User.username.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                    User.nickname.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                )
            )

        count_query = query.with_only_columns(func.count())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        sort_column = User.deleted_at
        if sort_by == "username":
            sort_column = User.username
        elif sort_by == "firstName":
            sort_column = User.first_name
        elif sort_by == "lastName":
            sort_column = User.last_name

        if descending:
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        query = query.offset(offset).limit(limit)
        result = await self.db.execute(query)
        users = result.scalars().all()

        return PaginatedResponse(
            items=[UserInfoResponse.from_model(u) for u in users],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def count_users_by_status(self) -> dict[UserStatus, int]:
        """Count live users grouped by status.

        Excludes soft-deleted users and super admins, so the numbers stay
        consistent with the listing endpoints (#103). Every status appears in
        the result, zero included; the ``registered`` bucket is reported here
        rather than dropped as ``list_users`` drops it.
        """
        query = (
            select(User.status, func.count())
            .where(User.deleted_at.is_(None), User.is_super_admin == 0)
            .group_by(User.status)
        )
        result = await self.db.execute(query)

        counts = {status: 0 for status in UserStatus}
        for status_value, count in result.all():
            counts[UserStatus(status_value)] = count
        return counts

    async def get_user(self, username: str) -> User:
        """Get user by username (includes soft-deleted)."""
        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)
        return user

    async def submit_for_review(self, username: str) -> User:
        """Transition a ``registered`` user to ``pending`` and notify admins.

        Triggered by the user via ``POST /v1/users/me/submit-for-review``
        once they have completed the second-stage signup (e.g. identity
        document upload). The ``user.registration_pending`` notification is
        deferred to this transition so admins do not see partial
        applications.
        """
        user = await self.get_user_or_raise(username)

        if user.status != UserStatus.registered.value:
            raise InvalidStateException(
                f"User is not registered (current status: {user.status})"
            )

        # With verification off there is no document to check (#428); the
        # step itself stays, as the way back after an admin's reconsider.
        if settings.identity_verification_required:
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
            if result.first() is None:
                raise IdentityDocumentRequiredException(username)

        user.status = UserStatus.pending.value
        await self.db.flush()

        await AuthService(self.db).notify_registration_pending(user)

        return user

    async def approve_user(
        self,
        username: str,
        actor_username: str | None = None,
        resolution_reason: str | None = None,
    ) -> User:
        """Approve a pending user. Closes any active review-request row."""
        from .user_review import RESOLUTION_APPROVED, UserReviewService

        user = await self.get_user_or_raise(username)

        if user.status != UserStatus.pending.value:
            raise InvalidStateException(
                f"User is not pending (current status: {user.status})"
            )

        user.status = UserStatus.active.value
        await self.db.flush()

        _ = await UserReviewService(self.db).close_active(
            username=username,
            actor_username=actor_username or username,
            resolution=RESOLUTION_APPROVED,
            resolution_reason=resolution_reason,
        )

        _ = await self._notifications.clear_actionable(
            "user_approval", pending_action_key=user.username
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="account.registration_approved",
                recipients=[user.username],
                data={"username": user.username},
            )
        )

        return user

    async def block_user(
        self,
        username: str,
        actor_username: str | None = None,
        resolution_reason: str | None = None,
    ) -> User:
        """Block a user. Closes any active review-request row."""
        from .user_review import RESOLUTION_BLOCKED, UserReviewService

        user = await self.get_user_or_raise(username)

        if user.is_super_admin:
            raise CannotBlockSuperAdminException()

        if user.status == UserStatus.blocked.value:
            raise InvalidStateException("User is already blocked")

        user.status = UserStatus.blocked.value
        await self.db.flush()

        _ = await UserReviewService(self.db).close_active(
            username=username,
            actor_username=actor_username or username,
            resolution=RESOLUTION_BLOCKED,
            resolution_reason=resolution_reason,
        )

        _ = await self._notifications.clear_actionable(
            "user_approval", pending_action_key=user.username
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.blocked",
                recipients=[user.username],
                data={"username": user.username},
            )
        )

        return user

    async def unblock_user(self, username: str) -> User:
        """Unblock a user."""
        user = await self.get_user_or_raise(username)

        if user.status != UserStatus.blocked.value:
            raise InvalidStateException(
                f"User is not blocked (current status: {user.status})"
            )

        user.status = UserStatus.active.value
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.unblocked",
                recipients=[user.username],
                data={"username": user.username},
            )
        )

        return user

    async def assign_role(
        self,
        username: str,
        role: Role,
        actor_username: str | None = None,
    ) -> User:
        """Assign a role to a user. ``actor_username`` identifies the
        admin or super-admin performing the change; it is included in
        the ``user.role_changed`` payload so the recipient can see who
        granted the role."""
        user = await self.get_user_or_raise(username)

        roles_data = (
            UserRoles.model_validate_json(user.roles) if user.roles else UserRoles()
        )
        existing_roles = set(roles_data.roles)

        if role.value in existing_roles:
            raise RoleAlreadyAssignedException(role.value)

        old_roles = sorted(existing_roles)
        existing_roles.add(role.value)
        new_roles = sorted(existing_roles)
        user.roles = UserRoles(roles=new_roles).model_dump_json()
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.role_changed",
                recipients=[user.username],
                data={
                    "username": user.username,
                    "oldRoles": old_roles,
                    "newRoles": new_roles,
                    "added": [role.value],
                    "removed": [],
                    "actorUsername": actor_username,
                },
            )
        )

        return user

    async def remove_role(
        self,
        username: str,
        role: Role,
        actor_username: str | None = None,
    ) -> User:
        """Remove a role from a user. See ``assign_role`` for the
        ``actor_username`` contract."""
        user = await self.get_user_or_raise(username)

        roles_data = (
            UserRoles.model_validate_json(user.roles) if user.roles else UserRoles()
        )
        existing_roles = set(roles_data.roles)

        if role.value not in existing_roles:
            raise RoleNotFoundException(role.value)

        old_roles = sorted(existing_roles)
        existing_roles.discard(role.value)
        new_roles = sorted(existing_roles)
        user.roles = UserRoles(roles=new_roles).model_dump_json()
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.role_changed",
                recipients=[user.username],
                data={
                    "username": user.username,
                    "oldRoles": old_roles,
                    "newRoles": new_roles,
                    "added": [],
                    "removed": [role.value],
                    "actorUsername": actor_username,
                },
            )
        )

        return user

    async def update_user(
        self,
        username: str,
        email: str | None = UNSET,
        first_name: str | None = UNSET,
        middle_name: str | None = UNSET,
        last_name: str | None = UNSET,
        nickname: str | None = UNSET,
        phone: str | None = UNSET,
        date_of_birth: int | None = UNSET,
        bio: str | None = UNSET,
        achievements: str | None = UNSET,
        emergency_contact: str | None = UNSET,
        medical_info: str | None = UNSET,
        use_name_publicly: bool | None = UNSET,
        is_public_profile: bool | None = UNSET,
        gender: str | None = UNSET,
        address: str | None = UNSET,
        actor_username: str | None = None,
    ) -> User:
        """Update a user's profile.

        Uses UNSET sentinel to distinguish between:
        - UNSET: don't change this field
        - None: clear this field (set to null)
        - value: set this field to value

        ``actor_username`` identifies who is performing the update.
        When it differs from ``username``, an admin-driven edit is
        notified to the target user via ``profile.changed_by_admin``.
        """
        user = await self.get_user_or_raise(username)

        changed_fields: list[str] = []

        def _track(field: str, sentinel: Any) -> None:
            if sentinel is not UNSET:
                changed_fields.append(field)

        _track("email", email)
        _track("firstName", first_name)
        _track("middleName", middle_name)
        _track("lastName", last_name)
        _track("nickname", nickname)
        _track("phone", phone)
        _track("dateOfBirthUtc", date_of_birth)
        _track("bio", bio)
        _track("achievements", achievements)
        _track("emergencyContact", emergency_contact)
        _track("medicalInfo", medical_info)
        _track("useNamePublicly", use_name_publicly)
        _track("gender", gender)
        _track("address", address)

        if email is not UNSET:
            user.email = email
        if first_name is not UNSET:
            user.first_name = first_name
        if middle_name is not UNSET:
            user.middle_name = middle_name
        if last_name is not UNSET:
            user.last_name = last_name
        if nickname is not UNSET:
            user.nickname = nickname
        if phone is not UNSET:
            user.phone = phone
        if date_of_birth is not UNSET:
            validate_utc_midnight(date_of_birth, "dateOfBirthUtc")
            user.date_of_birth = date_of_birth
        if bio is not UNSET:
            user.bio = bio
        if achievements is not UNSET:
            user.achievements = achievements
        if emergency_contact is not UNSET:
            user.emergency_contact = emergency_contact
        if medical_info is not UNSET:
            user.medical_info = medical_info
        if use_name_publicly is not UNSET:
            user.use_name_publicly = 1 if use_name_publicly else 0
        if gender is not UNSET:
            user.gender = gender
        if address is not UNSET:
            user.address = address

        # Public-profile visibility is user-owned: only the user themselves,
        # and only when they hold the coach role, may change it. Admins
        # cannot override it; non-coaches stay false. (#266)
        touches_public_flags = (
            is_public_profile is not UNSET or use_name_publicly is not UNSET
        )
        is_self = actor_username == username
        is_coach = "coach" in self._roles_list(user)
        if is_public_profile is not UNSET and is_self and is_coach:
            user.is_public_profile = 1 if is_public_profile else 0
            changed_fields.append("isPublicProfile")
        # Invariant: a public name only applies to a public profile. When a
        # coach edits these flags and the profile ends up non-public, force
        # use_name_publicly off too.
        if touches_public_flags and is_self and is_coach and not user.is_public_profile:
            user.use_name_publicly = 0

        await self.db.flush()

        if changed_fields and actor_username is not None and actor_username != username:
            await self._notifications.notify_for_event(
                NotificationEvent(
                    type="profile.changed_by_admin",
                    recipients=[username],
                    data={
                        "username": username,
                        "actorUsername": actor_username,
                        "changedFields": changed_fields,
                    },
                )
            )

        return user

    async def create_user(
        self,
        username: str,
        password_hash: str,
        email: str | None = None,
        first_name: str | None = None,
        middle_name: str | None = None,
        last_name: str | None = None,
        nickname: str | None = None,
        phone: str | None = None,
        date_of_birth: int | None = None,
        use_name_publicly: bool | None = None,
        bio: str | None = None,
        medical_info: str | None = None,
        emergency_contact: str | None = None,
        achievements: str | None = None,
        gender: str | None = None,
        address: str | None = None,
        status: UserStatus = UserStatus.active,
    ) -> User:
        """Create a new user (admin only)."""
        validate_utc_midnight(date_of_birth, "dateOfBirthUtc")
        created_at = now_utc_ms()

        user = User(
            username=username,
            password=AuthService.hash_password(password_hash),
            email=email,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            nickname=nickname,
            phone=phone,
            date_of_birth=date_of_birth,
            use_name_publicly=1 if use_name_publicly else 0,
            bio=bio,
            medical_info=medical_info,
            emergency_contact=emergency_contact,
            achievements=achievements,
            gender=gender,
            address=address,
            status=status.value,
            roles="{}",
            created_at=created_at,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def delete_user(self, username: str) -> User:
        """Soft delete a user. A super admin cannot be deleted (#459).

        Deleted rows are looked up too, so a second delete is refused as
        already deleted rather than answered as an unknown user (#523).
        """
        user = await self.get_user(username)

        if user.is_super_admin:
            raise CannotBlockSuperAdminException()

        if user.deleted_at is not None:
            raise UserAlreadyDeletedException(username)

        admins = [a for a in await self._list_admin_usernames() if a != username]

        user.deleted_at = now_utc_ms()
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.deleted",
                recipients=admins,
                data={"username": username},
            )
        )

        return user

    async def restore_user(self, username: str) -> User:
        """Restore a soft-deleted user."""
        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)

        if user.deleted_at is None:
            raise NothingToRestoreException("User", username)

        user.deleted_at = None
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.restored",
                recipients=[username],
                data={"username": username},
            )
        )

        return user

    async def hard_delete_user(self, username: str, actor: str | None = None) -> None:
        """Permanently delete a user and all their data. Super admin only.

        Requires the user to be soft-deleted first (deleted_at must be set).
        Events the user organized are not orphaned: ``actor``, the super
        admin performing the delete, takes them over (#386), and so do the
        evaluation templates they created, live or soft-deleted (#490). Their
        coach assignments, enrollments and attendance go by database CASCADE.
        """
        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)

        if user.is_super_admin:
            raise CannotBlockSuperAdminException()

        if user.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("User", username)

        # Clean up actionable notifications whose pending action targets the
        # user being removed — those actions can never resolve once the
        # subject is gone. Informational notifications referencing this user
        # only inside ``payload.data`` (no ``pending_action_key``) are left
        # alone — they remain a legitimate historical record for recipients.
        _ = await self._notifications.clear_actionable_by_key(username)

        if actor is not None and actor != username:
            _ = await self.db.execute(
                update(EventSchedule)
                .where(EventSchedule.organizer_name == username)
                .values(organizer_name=actor)
            )
            _ = await self.db.execute(
                update(OccurrenceOverride)
                .where(OccurrenceOverride.new_organizer_name == username)
                .values(new_organizer_name=actor)
            )
            _ = await self.db.execute(
                update(EvaluationTemplate)
                .where(EvaluationTemplate.created_by == username)
                .values(created_by=actor)
            )

        await self.db.delete(user)
        await self.db.flush()

    async def mark_left(self, username: str) -> User:
        """Mark an active user as left.

        Only ``active`` users can leave (#513): reactivation makes a left
        user ``active``, so marking a registered, pending or blocked user
        left would let reactivation bypass approval or a block.
        """
        user = await self.get_user_or_raise(username)

        if user.status == UserStatus.left.value:
            raise InvalidStateException("User is already marked as left")

        if user.is_super_admin:
            raise CannotBlockSuperAdminException()

        if user.status != UserStatus.active.value:
            raise UserNotActiveException(username)

        user.status = UserStatus.left.value
        await self.db.flush()
        return user

    async def reactivate_user(self, username: str) -> User:
        """Reactivate a user who left."""
        user = await self.get_user_or_raise(username)

        if user.status != UserStatus.left.value:
            raise InvalidStateException(
                f"User is not marked as left (current status: {user.status})"
            )

        user.status = UserStatus.active.value
        await self.db.flush()
        return user

    async def transfer_superadmin(self, from_username: str, to_username: str) -> User:
        """Transfer super admin role from one user to another.

        Emits ``user.role_changed`` to both affected users, matching the
        shape used by ``assign_role`` / ``remove_role`` so clients can
        render either direction with the same formatter. The actor is
        the outgoing super-admin (the only caller authorised by
        ``require_super_admin``)."""
        from_user = await self.get_user_or_raise(from_username)
        to_user = await self.get_user_or_raise(to_username)

        if not from_user.is_super_admin:
            raise CannotTransferFromNonSuperAdminException()

        if from_username == to_username:
            raise CannotTransferToSelfException()

        from_user.is_super_admin = 0
        to_user.is_super_admin = 1

        await self.db.flush()

        from_roles = self._roles_list(from_user)
        to_roles = self._roles_list(to_user)

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.role_changed",
                recipients=[from_user.username],
                data={
                    "username": from_user.username,
                    "oldRoles": sorted(set(from_roles) | {"super_admin"}),
                    "newRoles": sorted(from_roles),
                    "added": [],
                    "removed": ["super_admin"],
                    "actorUsername": from_username,
                },
            )
        )
        await self._notifications.notify_for_event(
            NotificationEvent(
                type="user.role_changed",
                recipients=[to_user.username],
                data={
                    "username": to_user.username,
                    "oldRoles": sorted(to_roles),
                    "newRoles": sorted(set(to_roles) | {"super_admin"}),
                    "added": ["super_admin"],
                    "removed": [],
                    "actorUsername": from_username,
                },
            )
        )

        return to_user
