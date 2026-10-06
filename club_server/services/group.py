"""Group service for group management operations."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..age_eligibility import (
    Age,
    EligibilityWindow,
    decode_age,
    encode_age,
    is_inverted_band,
)
from ..db.models.group import Group, GroupMember
from ..db.models.group_join_request import GroupJoinRequest, JoinRequestStatus
from ..db.models.user import User, UserStatus
from .notification import NotificationEvent, NotificationService
from ..exceptions import (
    AlreadyMemberException,
    AutoGroupModificationException,
    AutoGroupNotJoinableException,
    GroupNotFoundException,
    HardDeleteNeedsSoftDeleteException,
    InvalidStateException,
    JoinRequestNotFoundException,
    JoinRequestNotPendingException,
    JoinRequestPendingException,
    MemberNotFoundException,
    MembersExistException,
    MembersIneligibleException,
    NotEligibleException,
    NothingToRestoreException,
    UserNotFoundException,
)
from ..schemas.common import ChangeLog, PaginatedResponse, UserRoles
from ..schemas.group import GroupResponse
from ..utils import MS_PER_DAY, now_utc_ms


KIND_MANUAL = "manual"
KIND_SEMI_AUTO = "semi_auto"
KIND_AUTO = "auto"

INVERTED_BAND_MESSAGE = "minAge must not be greater than maxAge"


def _has_criteria(
    min_age: str | None,
    max_age: str | None,
    gender: str | None,
) -> bool:
    return any(v is not None for v in (min_age, max_age, gender))


def _compute_kind(
    min_age: str | None,
    max_age: str | None,
    gender: str | None,
    semi_auto: bool,
) -> str:
    """Derive group kind from criteria + the semi_auto flag.

    No criteria → manual (flag ignored).
    Criteria + semi_auto=True → semi_auto.
    Criteria + semi_auto=False → auto.
    """
    if not _has_criteria(min_age, max_age, gender):
        return KIND_MANUAL
    return KIND_SEMI_AUTO if semi_auto else KIND_AUTO


def _user_is_staff(user: User) -> bool:
    """True for super-admins and users carrying the admin or coach role."""
    if user.is_super_admin:
        return True
    if not user.roles:
        return False
    parsed = UserRoles.model_validate_json(user.roles)
    roles = set(parsed.roles)
    return "admin" in roles or "coach" in roles


def _user_matches_criteria(
    user: User, group: Group, window: EligibilityWindow | None = None
) -> bool:
    """Pure-Python eligibility check against a group's gender and age band.

    The band is checked as the window of birth dates it comes to today
    (eligibility R4); both ends are inclusive at the day level.
    """
    if group.gender is not None and user.gender != group.gender:
        return False
    window = group.eligibility_window if window is None else window
    return window.admits(user.date_of_birth)


def _build_auto_member_query(group: Group):
    """Build a query for users matching a group's gender and today's window.

    Both ends of the window are inclusive at the day level: the upper-bound
    check uses `< dob_on_or_before_utc + 1 day` so users born any time on
    that day match.
    """
    window = group.eligibility_window
    query = select(User).where(
        User.deleted_at.is_(None),
        User.status == UserStatus.active.value,
        User.is_super_admin == 0,
    )

    if group.gender is not None:
        query = query.where(User.gender == group.gender)

    if window.dob_on_or_after_utc is not None:
        query = query.where(User.date_of_birth.isnot(None))
        query = query.where(User.date_of_birth >= window.dob_on_or_after_utc)

    if window.dob_on_or_before_utc is not None:
        query = query.where(User.date_of_birth.isnot(None))
        query = query.where(
            User.date_of_birth < window.dob_on_or_before_utc + MS_PER_DAY
        )

    return query


def is_eligible_for_semi_auto(
    user: User, group: Group, window: EligibilityWindow | None = None
) -> bool:
    """Semi-auto eligibility: staff are exempt; everyone else must match criteria.

    ``window`` is the group's window on the day in question; today's when
    it is left out.
    """
    if _user_is_staff(user):
        return True
    return _user_matches_criteria(user, group, window)


def is_member_eligible(group: Group, user: User | None) -> bool:
    """Whether a member of ``group`` still meets its criteria today (R82).

    Only a semi-auto group stores members it also has criteria for: a manual
    group has none, and an auto group's members are those who match.
    """
    if group.kind != KIND_SEMI_AUTO or user is None:
        return True
    return is_eligible_for_semi_auto(user, group)


class GroupService:
    """Service for group management operations."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._notifications: NotificationService = NotificationService(db)

    async def _list_admin_usernames(self) -> list[str]:
        """Recipients for group-admin-facing events.

        Admins are identified by carrying the ``admin`` role in the
        ``users.roles`` JSON; super-admins are included as a superset.
        Deleted/inactive users are excluded.
        """
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
            if "admin" in parsed.roles:
                admins.append(u.username)
        return admins

    async def ineligible_member_count(self, group: Group) -> int:
        """How many stored members no longer meet the group's criteria (R83).

        Read with its own query, so it does not depend on which of the
        group's relationships the caller happened to load.
        """
        if group.kind != KIND_SEMI_AUTO:
            return 0
        users = (
            await self.db.execute(
                select(User)
                .join(GroupMember, GroupMember.membername == User.username)
                .where(GroupMember.group_id == group.id)
            )
        ).scalars()
        window = group.eligibility_window
        return sum(
            1 for user in users if not is_eligible_for_semi_auto(user, group, window)
        )

    async def to_response(
        self, group: Group, member_count: int = 0, requested: bool = False
    ) -> GroupResponse:
        """The group as the API returns it, with its ineligible-member count."""
        return GroupResponse.from_model(
            group,
            member_count,
            requested,
            ineligible_member_count=await self.ineligible_member_count(group),
        )

    async def admin_usernames(self) -> list[str]:
        """Who is told about a group on the admins' behalf (R84)."""
        return await self._list_admin_usernames()

    async def _list_member_usernames(self, group: Group) -> list[str]:
        """Recipients for member-facing events.

        Uses the explicit ``group_members`` rows for manual/semi_auto
        groups. Auto groups have no stored memberships and are skipped
        here (informational events on auto groups would need a separate
        broadcast surface, out of scope for #47).
        """
        if group.kind == KIND_AUTO:
            return []
        result = await self.db.execute(
            select(GroupMember.membername).where(GroupMember.group_id == group.id)
        )
        return [row for (row,) in result.all()]

    async def _count_auto_members(self, group: Group) -> int:
        query = _build_auto_member_query(group)
        count_query = query.with_only_columns(func.count())
        result = await self.db.execute(count_query)
        return result.scalar_one()

    async def _get_member_count(self, group: Group) -> int:
        if group.kind == KIND_AUTO:
            return await self._count_auto_members(group)
        return len(group.members)

    async def get_groups(
        self,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[GroupResponse]:
        """Get a paginated list of groups with member counts (excludes soft-deleted)."""
        count_query = (
            select(func.count()).select_from(Group).where(Group.deleted_at.is_(None))
        )
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            select(Group)
            .where(Group.deleted_at.is_(None))
            .options(selectinload(Group.members))
            .offset(offset)
            .limit(limit)
            .order_by(Group.name)
        )
        result = await self.db.execute(query)
        groups = result.scalars().all()

        items = []
        for g in groups:
            count = await self._get_member_count(g)
            items.append(await self.to_response(g, count))

        return PaginatedResponse(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
        )

    async def list_deleted_groups(
        self,
        offset: int = 0,
        limit: int = 20,
    ) -> PaginatedResponse[GroupResponse]:
        """List soft-deleted groups (admin only)."""
        count_query = (
            select(func.count()).select_from(Group).where(Group.deleted_at.isnot(None))
        )
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            select(Group)
            .where(Group.deleted_at.isnot(None))
            .options(selectinload(Group.members))
            .offset(offset)
            .limit(limit)
            .order_by(Group.deleted_at.desc())
        )
        result = await self.db.execute(query)
        groups = result.scalars().all()

        return PaginatedResponse(
            items=[await self.to_response(g, len(g.members)) for g in groups],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def get_group(self, group_id: int) -> Group:
        """Get a group by ID with members loaded (includes soft-deleted)."""
        result = await self.db.execute(
            select(Group)
            .where(Group.id == group_id)
            .options(selectinload(Group.members).selectinload(GroupMember.user))
        )
        group = result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        return group

    async def get_auto_group_members(
        self,
        group: Group,
        sort_by: str | None = None,
        descending: bool = False,
    ) -> list[GroupMember]:
        """Get dynamically computed members for an auto group."""
        query = _build_auto_member_query(group)

        if sort_by:
            sort_col = User.username
            if sort_by == "firstName":
                sort_col = User.first_name
            elif sort_by == "lastName":
                sort_col = User.last_name
            elif sort_by == "nickname":
                sort_col = User.nickname
            query = query.order_by(sort_col.desc() if descending else sort_col.asc())
        else:
            query = query.order_by(User.username.asc())

        result = await self.db.execute(query)
        users = result.scalars().all()

        members = []
        for u in users:
            gm = GroupMember(group_id=group.id, membername=u.username)
            gm.user = u
            members.append(gm)
        return members

    async def create_group(
        self,
        name: str,
        description: str | None = None,
        min_age: Age | None = None,
        max_age: Age | None = None,
        strict_age: bool = False,
        gender: str | None = None,
        semi_auto: bool = False,
    ) -> Group:
        """Create a new group."""
        if is_inverted_band(min_age, max_age):
            raise InvalidStateException(INVERTED_BAND_MESSAGE)

        kind = _compute_kind(
            encode_age(min_age), encode_age(max_age), gender, semi_auto
        )

        now = now_utc_ms()
        group = Group(
            name=name,
            description=description,
            kind=kind,
            gender=gender,
            min_age=encode_age(min_age),
            max_age=encode_age(max_age),
            strict_age=strict_age,
            created_at=now,
        )
        self.db.add(group)
        await self.db.flush()
        return group

    async def update_group(
        self,
        group_id: int,
        name: str | None = None,
        description: str | None = None,
        min_age: Age | None = None,
        max_age: Age | None = None,
        strict_age: bool | None = None,
        gender: str | None = None,
        semi_auto: bool | None = None,
        fields_set: set[str] | None = None,
    ) -> tuple[Group, ChangeLog]:
        """Update a group. Returns (group, changes)."""
        result = await self.db.execute(
            select(Group)
            .where(Group.id == group_id)
            .options(selectinload(Group.members).selectinload(GroupMember.user))
        )
        group = result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        fields_set = fields_set or set()
        changes = ChangeLog()

        if name is not None:
            changes.add("name", group.name, name)
            group.name = name
        # `in fields_set` rather than `is not None`, so an explicit null
        # clears the value instead of being read as "field omitted" (#326).
        if "description" in fields_set:
            changes.add("description", group.description, description)
            group.description = description

        if "min_age" in fields_set:
            changes.add("min_age", group.min_age, encode_age(min_age))
            group.min_age = encode_age(min_age)

        if "max_age" in fields_set:
            changes.add("max_age", group.max_age, encode_age(max_age))
            group.max_age = encode_age(max_age)

        if strict_age is not None:
            changes.add("strict_age", bool(group.strict_age), strict_age)
            group.strict_age = strict_age

        if "gender" in fields_set:
            changes.add("gender", group.gender, gender)
            group.gender = gender

        if is_inverted_band(decode_age(group.min_age), decode_age(group.max_age)):
            raise InvalidStateException(INVERTED_BAND_MESSAGE)

        # Determine the requested kind. If semi_auto isn't in the payload,
        # preserve the current group's mode where possible: a group already
        # `semi_auto` should stay `semi_auto`; an `auto` group should stay
        # `auto`; a `manual` group becomes `auto` only when criteria appear
        # without `semi_auto: true`.
        if "semi_auto" in fields_set and semi_auto is not None:
            requested_semi_auto = semi_auto
        else:
            requested_semi_auto = group.kind == KIND_SEMI_AUTO

        new_kind = _compute_kind(
            group.min_age,
            group.max_age,
            group.gender,
            requested_semi_auto,
        )

        # Manual → auto with members → reject
        if (
            group.kind == KIND_MANUAL
            and new_kind == KIND_AUTO
            and len(group.members) > 0
        ):
            raise MembersExistException(group.id, len(group.members))

        # Manual → semi_auto, or semi_auto criteria edit, with members →
        # require every existing approved member to satisfy the new criteria.
        # `group` is already mutated in place with the proposed criteria above,
        # so `is_eligible_for_semi_auto(m.user, group)` evaluates against the
        # new rules, not the old ones. semi_auto → manual is intentionally not
        # validated: a manual group has no criteria, so any member is trivially
        # eligible. Issue #93 / SDK #174.
        if (
            new_kind == KIND_SEMI_AUTO
            and group.kind in (KIND_MANUAL, KIND_SEMI_AUTO)
            and len(group.members) > 0
        ):
            ineligible = []
            for m in group.members:
                if m.user is None:
                    continue
                if not is_eligible_for_semi_auto(m.user, group):
                    ineligible.append(m.membername)
            if ineligible:
                raise MembersIneligibleException(group.id, ineligible)

        if group.kind != new_kind:
            changes.add("kind", group.kind, new_kind)
            group.kind = new_kind

        await self.db.flush()

        if changes:
            await self._notifications.notify_for_event(
                NotificationEvent(
                    type="group.settings_changed",
                    recipients=await self._list_member_usernames(group),
                    data={
                        "groupId": group.id,
                        "groupName": group.name,
                        "changes": {
                            field: {"from": c.old, "to": c.new}
                            for field, c in changes.changes.items()
                        },
                    },
                )
            )
        return group, changes

    async def soft_delete_group(self, group_id: int) -> Group:
        """Soft delete a group by setting deleted_at."""
        result = await self.db.execute(
            select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
        )
        group = result.scalar_one_or_none()
        if not group:
            raise GroupNotFoundException(group_id)

        recipients = await self._list_member_usernames(group)
        group_name = group.name

        group.deleted_at = now_utc_ms()
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="group.archived",
                recipients=recipients,
                data={"groupId": group_id, "groupName": group_name},
            )
        )
        return group

    async def restore_group(self, group_id: int) -> Group:
        """Restore a soft-deleted group."""
        result = await self.db.execute(select(Group).where(Group.id == group_id))
        group = result.scalar_one_or_none()
        if not group:
            raise GroupNotFoundException(group_id)

        if group.deleted_at is None:
            raise NothingToRestoreException("Group", group_id)

        group.deleted_at = None
        await self.db.flush()
        return group

    async def hard_delete_group(self, group_id: int) -> str:
        """Hard delete a group. Requires soft-delete first."""
        result = await self.db.execute(select(Group).where(Group.id == group_id))
        group = result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        if group.deleted_at is None:
            raise HardDeleteNeedsSoftDeleteException("Group", group_id)

        group_name = group.name

        await self.db.execute(
            delete(GroupJoinRequest).where(GroupJoinRequest.group_id == group_id)
        )
        await self.db.execute(
            delete(GroupMember).where(GroupMember.group_id == group_id)
        )
        await self.db.execute(delete(Group).where(Group.id == group_id))
        await self.db.flush()
        return group_name

    async def list_group_members(
        self,
        group_id: int,
        sort_by: str | None = None,
        descending: bool = False,
    ) -> list[GroupMember]:
        """List members of a group with user details."""
        result = await self.db.execute(
            select(Group)
            .where(Group.id == group_id)
            .options(selectinload(Group.members).selectinload(GroupMember.user))
        )
        group = result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        if group.kind == KIND_AUTO:
            return await self.get_auto_group_members(group, sort_by, descending)

        members = list(group.members)

        if sort_by:
            if sort_by == "membername":
                members.sort(key=lambda m: m.membername or "", reverse=descending)
            elif sort_by == "firstName":
                members.sort(
                    key=lambda m: (m.user.first_name or "") if m.user else "",
                    reverse=descending,
                )
            elif sort_by == "lastName":
                members.sort(
                    key=lambda m: (m.user.last_name or "") if m.user else "",
                    reverse=descending,
                )
            elif sort_by == "nickname":
                members.sort(
                    key=lambda m: (m.user.nickname or "") if m.user else "",
                    reverse=descending,
                )

        return members

    async def _resolve_pending_request_as_approved(
        self, group_id: int, membername: str, actor_username: str
    ) -> None:
        """If a pending request exists for (group, user), mark it approved
        and delete its actionable notices, as approving it does (#524)."""
        result = await self.db.execute(
            select(GroupJoinRequest).where(
                GroupJoinRequest.group_id == group_id,
                GroupJoinRequest.username == membername,
                GroupJoinRequest.status == JoinRequestStatus.pending.value,
            )
        )
        req = result.scalar_one_or_none()
        if req is None:
            return
        req.status = JoinRequestStatus.approved.value
        req.decided_at = now_utc_ms()
        req.decided_by = actor_username
        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "group_join_request", pending_action_id=req.id
        )

    async def add_group_member(
        self,
        group_id: int,
        membername: str,
        actor_username: str,
    ) -> tuple[GroupMember, User, str]:
        """Add a member to a group; auto-resolves any pending join request."""
        group_result = await self.db.execute(select(Group).where(Group.id == group_id))
        group = group_result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        if group.kind == KIND_AUTO:
            raise AutoGroupModificationException(group_id)

        user_result = await self.db.execute(
            select(User).where(
                User.username == membername,
                User.deleted_at.is_(None),
                User.is_super_admin == 0,
            )
        )
        user = user_result.scalar_one_or_none()

        if not user:
            raise UserNotFoundException(membername)

        if group.kind == KIND_SEMI_AUTO and not is_eligible_for_semi_auto(user, group):
            raise NotEligibleException(membername, group_id)

        existing = await self.db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.membername == membername
            )
        )
        if existing.scalar_one_or_none():
            raise AlreadyMemberException(membername, group_id)

        member = GroupMember(group_id=group_id, membername=membername)
        self.db.add(member)
        await self.db.flush()

        await self._resolve_pending_request_as_approved(
            group_id, membername, actor_username
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="group.member_added",
                recipients=[membername],
                data={
                    "groupId": group.id,
                    "groupName": group.name,
                    "addedByUsername": actor_username,
                },
            )
        )

        return member, user, group.name

    async def remove_group_member(
        self,
        group_id: int,
        membername: str,
    ) -> Group:
        """Remove a member from a group."""
        group_result = await self.db.execute(select(Group).where(Group.id == group_id))
        group = group_result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        if group.kind == KIND_AUTO:
            raise AutoGroupModificationException(group_id)

        member_result = await self.db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.membername == membername
            )
        )
        member = member_result.scalar_one_or_none()

        if not member:
            raise MemberNotFoundException(membername, group_id)

        await self.db.delete(member)
        await self.db.flush()
        await self.db.refresh(group)

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="group.member_removed",
                recipients=[membername],
                data={"groupId": group.id, "groupName": group.name},
            )
        )
        return group

    async def get_user_groups(self, username: str) -> list[GroupResponse]:
        """Get all groups a user belongs to (manual+semi_auto via memberships, plus matching auto groups)."""
        user_result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()

        if not user:
            raise UserNotFoundException(username)

        explicit_result = await self.db.execute(
            select(Group)
            .join(GroupMember, Group.id == GroupMember.group_id)
            .where(
                GroupMember.membername == username,
                Group.deleted_at.is_(None),
                Group.kind != KIND_AUTO,
            )
            .options(selectinload(Group.members))
            .order_by(Group.name)
        )
        explicit_groups = list(explicit_result.scalars().all())

        auto_result = await self.db.execute(
            select(Group).where(
                Group.deleted_at.is_(None),
                Group.kind == KIND_AUTO,
            )
        )
        auto_groups_all = auto_result.scalars().all()

        matching_auto = [g for g in auto_groups_all if _user_matches_criteria(user, g)]

        responses = []
        for g in explicit_groups:
            responses.append(await self.to_response(g, len(g.members)))
        for g in matching_auto:
            count = await self._count_auto_members(g)
            responses.append(await self.to_response(g, count))

        responses.sort(key=lambda r: r.name)
        return responses

    async def add_members_bulk(
        self,
        group_id: int,
        membernames: list[str],
        actor_username: str,
    ) -> tuple[list[str], list[str], list[str], list[str], str]:
        """Add multiple members. Returns (added, already_members, not_found, not_eligible, group_name)."""
        group_result = await self.db.execute(select(Group).where(Group.id == group_id))
        group = group_result.scalar_one_or_none()

        if not group:
            raise GroupNotFoundException(group_id)

        if group.kind == KIND_AUTO:
            raise AutoGroupModificationException(group_id)

        added: list[str] = []
        already_members: list[str] = []
        not_found: list[str] = []
        not_eligible: list[str] = []

        for membername in membernames:
            user_result = await self.db.execute(
                select(User).where(
                    User.username == membername,
                    User.deleted_at.is_(None),
                    User.is_super_admin == 0,
                )
            )
            user = user_result.scalar_one_or_none()

            if not user:
                not_found.append(membername)
                continue

            existing = await self.db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.membername == membername,
                )
            )
            if existing.scalar_one_or_none():
                already_members.append(membername)
                continue

            if group.kind == KIND_SEMI_AUTO and not is_eligible_for_semi_auto(
                user, group
            ):
                not_eligible.append(membername)
                continue

            member = GroupMember(group_id=group_id, membername=membername)
            self.db.add(member)
            added.append(membername)

        await self.db.flush()

        for name in added:
            await self._resolve_pending_request_as_approved(
                group_id, name, actor_username
            )

        for name in added:
            await self._notifications.notify_for_event(
                NotificationEvent(
                    type="group.member_added",
                    recipients=[name],
                    data={
                        "groupId": group.id,
                        "groupName": group.name,
                        "addedByUsername": actor_username,
                    },
                )
            )

        return added, already_members, not_found, not_eligible, group.name

    # =========================================================================
    # Eligibility queries
    # =========================================================================

    async def list_eligible_users(self, group_id: int) -> list[User]:
        """Users who can be added to (or who can request to join) this group."""
        result = await self.db.execute(
            select(Group)
            .where(Group.id == group_id, Group.deleted_at.is_(None))
            .options(selectinload(Group.members))
        )
        group = result.scalar_one_or_none()
        if not group:
            raise GroupNotFoundException(group_id)

        if group.kind == KIND_AUTO:
            raise AutoGroupNotJoinableException(group_id)

        existing_members = {m.membername for m in group.members}

        pending_q = await self.db.execute(
            select(GroupJoinRequest.username).where(
                GroupJoinRequest.group_id == group_id,
                GroupJoinRequest.status == JoinRequestStatus.pending.value,
            )
        )
        pending = {row for (row,) in pending_q.all()}

        users_q = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
                User.is_super_admin == 0,
            )
        )
        all_users = users_q.scalars().all()

        eligible: list[User] = []
        for u in all_users:
            if u.username in existing_members or u.username in pending:
                continue
            if group.kind == KIND_SEMI_AUTO:
                if not is_eligible_for_semi_auto(u, group):
                    continue
            eligible.append(u)

        eligible.sort(key=lambda u: u.username)
        return eligible

    async def get_user_group(self, username: str, group_id: int) -> GroupResponse:
        """Return a single group's info if the user has any relation to it.

        Relation = current member, or any join request (any status).
        """
        user_result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)

        group_result = await self.db.execute(
            select(Group)
            .where(Group.id == group_id, Group.deleted_at.is_(None))
            .options(selectinload(Group.members))
        )
        group = group_result.scalar_one_or_none()
        if not group:
            raise GroupNotFoundException(group_id)

        member_exists = await self.db.execute(
            select(GroupMember.group_id).where(
                GroupMember.group_id == group_id,
                GroupMember.membername == username,
            )
        )
        has_membership = member_exists.scalar_one_or_none() is not None

        has_request = False
        has_pending = False
        if not has_membership:
            req_result = await self.db.execute(
                select(GroupJoinRequest.status).where(
                    GroupJoinRequest.group_id == group_id,
                    GroupJoinRequest.username == username,
                )
            )
            statuses = [row for (row,) in req_result.all()]
            has_request = len(statuses) > 0
            has_pending = JoinRequestStatus.pending.value in statuses

        if not (has_membership or has_request):
            raise GroupNotFoundException(group_id)

        return await self.to_response(group, len(group.members), requested=has_pending)

    async def list_eligible_groups(self, username: str) -> list[GroupResponse]:
        """Groups the user is allowed to *request* to join."""
        user_result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)

        joined_q = await self.db.execute(
            select(GroupMember.group_id).where(GroupMember.membername == username)
        )
        joined = {row for (row,) in joined_q.all()}

        pending_q = await self.db.execute(
            select(GroupJoinRequest.group_id).where(
                GroupJoinRequest.username == username,
                GroupJoinRequest.status == JoinRequestStatus.pending.value,
            )
        )
        pending = {row for (row,) in pending_q.all()}

        groups_q = await self.db.execute(
            select(Group)
            .where(
                Group.deleted_at.is_(None),
                Group.kind != KIND_AUTO,
            )
            .options(selectinload(Group.members))
            .order_by(Group.name)
        )
        all_groups = groups_q.scalars().all()

        responses: list[GroupResponse] = []
        for g in all_groups:
            if g.id in joined:
                continue
            if g.kind == KIND_SEMI_AUTO and not is_eligible_for_semi_auto(user, g):
                continue
            responses.append(
                await self.to_response(g, len(g.members), requested=g.id in pending)
            )

        return responses

    # =========================================================================
    # Join requests
    # =========================================================================

    async def create_join_request(
        self,
        group_id: int,
        username: str,
        reason: str | None = None,
    ) -> GroupJoinRequest:
        group_result = await self.db.execute(
            select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
        )
        group = group_result.scalar_one_or_none()
        if not group:
            raise GroupNotFoundException(group_id)

        if group.kind == KIND_AUTO:
            raise AutoGroupNotJoinableException(group_id)

        user_result = await self.db.execute(
            select(User).where(
                User.username == username,
                User.deleted_at.is_(None),
                User.is_super_admin == 0,
            )
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise UserNotFoundException(username)

        existing_member = await self.db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.membername == username,
            )
        )
        if existing_member.scalar_one_or_none():
            raise AlreadyMemberException(username, group_id)

        # Look up any existing request row for this (group, user). The
        # schema enforces at most one row per pair; status records its
        # current lifecycle state.
        existing_result = await self.db.execute(
            select(GroupJoinRequest).where(
                GroupJoinRequest.group_id == group_id,
                GroupJoinRequest.username == username,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing and existing.status == JoinRequestStatus.pending.value:
            raise JoinRequestPendingException(username, group_id)

        if group.kind == KIND_SEMI_AUTO and not is_eligible_for_semi_auto(user, group):
            raise NotEligibleException(username, group_id)

        now = now_utc_ms()
        if existing:
            # Revive the terminal row (cancelled / rejected / approved
            # but no longer a member). Same id is reused; lifecycle
            # history lives in audit_log.
            existing.status = JoinRequestStatus.pending.value
            existing.requested_at = now
            existing.decided_at = None
            existing.decided_by = None
            existing.reason = reason
            req = existing
        else:
            req = GroupJoinRequest(
                group_id=group_id,
                username=username,
                status=JoinRequestStatus.pending.value,
                requested_at=now,
                reason=reason,
            )
            req.group = group
            self.db.add(req)
        await self.db.flush()

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="group.join_request",
                recipients=await self._list_admin_usernames(),
                data={
                    "groupId": group.id,
                    "groupName": group.name,
                    "requesterUsername": username,
                    "reason": reason,
                },
                pending_action_type="group_join_request",
                pending_action_id=req.id,
            )
        )
        return req

    async def cancel_join_request(
        self,
        request_id: int,
        username: str,
    ) -> GroupJoinRequest:
        result = await self.db.execute(
            select(GroupJoinRequest).where(GroupJoinRequest.id == request_id)
        )
        req = result.scalar_one_or_none()
        if req is None:
            raise JoinRequestNotFoundException(request_id)
        if req.username != username:
            raise JoinRequestNotFoundException(request_id)
        if req.status != JoinRequestStatus.pending.value:
            raise JoinRequestNotPendingException(request_id, req.status)

        req.status = JoinRequestStatus.cancelled.value
        req.decided_at = now_utc_ms()
        req.decided_by = username
        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "group_join_request", pending_action_id=req.id
        )
        return req

    async def list_group_requests(
        self,
        group_id: int,
        status_filter: str | None = None,
    ) -> list[GroupJoinRequest]:
        group_result = await self.db.execute(select(Group).where(Group.id == group_id))
        if group_result.scalar_one_or_none() is None:
            raise GroupNotFoundException(group_id)

        query = select(GroupJoinRequest).where(GroupJoinRequest.group_id == group_id)
        if status_filter:
            query = query.where(GroupJoinRequest.status == status_filter)
        query = query.order_by(GroupJoinRequest.requested_at.desc())

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_user_requests(
        self,
        username: str,
        status_filter: str | None = None,
    ) -> list[GroupJoinRequest]:
        query = select(GroupJoinRequest).where(GroupJoinRequest.username == username)
        if status_filter:
            query = query.where(GroupJoinRequest.status == status_filter)
        query = query.order_by(GroupJoinRequest.requested_at.desc())

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def approve_request(
        self,
        group_id: int,
        request_id: int,
        actor_username: str,
    ) -> tuple[GroupJoinRequest, bool]:
        """Approve a pending request. Returns (request, member_added)."""
        result = await self.db.execute(
            select(GroupJoinRequest).where(
                GroupJoinRequest.id == request_id,
                GroupJoinRequest.group_id == group_id,
            )
        )
        req = result.scalar_one_or_none()
        if req is None:
            raise JoinRequestNotFoundException(request_id)
        if req.status != JoinRequestStatus.pending.value:
            raise JoinRequestNotPendingException(request_id, req.status)

        group_result = await self.db.execute(
            select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
        )
        group = group_result.scalar_one_or_none()
        if group is None:
            raise GroupNotFoundException(group_id)
        if group.kind == KIND_AUTO:
            raise AutoGroupNotJoinableException(group_id)

        user_result = await self.db.execute(
            select(User).where(User.username == req.username, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise UserNotFoundException(req.username)

        if group.kind == KIND_SEMI_AUTO and not is_eligible_for_semi_auto(user, group):
            raise NotEligibleException(req.username, group_id)

        existing = await self.db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.membername == req.username,
            )
        )
        member_added = False
        if existing.scalar_one_or_none() is None:
            self.db.add(GroupMember(group_id=group_id, membername=req.username))
            member_added = True

        req.status = JoinRequestStatus.approved.value
        req.decided_at = now_utc_ms()
        req.decided_by = actor_username
        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "group_join_request", pending_action_id=req.id
        )

        await self._notifications.notify_for_event(
            NotificationEvent(
                type="group.join_response",
                recipients=[req.username],
                data={
                    "groupId": group.id,
                    "groupName": group.name,
                    "outcome": "approved",
                    "actorUsername": actor_username,
                },
            )
        )
        return req, member_added

    async def reject_request(
        self,
        group_id: int,
        request_id: int,
        actor_username: str,
        reason: str | None = None,
    ) -> GroupJoinRequest:
        result = await self.db.execute(
            select(GroupJoinRequest).where(
                GroupJoinRequest.id == request_id,
                GroupJoinRequest.group_id == group_id,
            )
        )
        req = result.scalar_one_or_none()
        if req is None:
            raise JoinRequestNotFoundException(request_id)
        if req.status != JoinRequestStatus.pending.value:
            raise JoinRequestNotPendingException(request_id, req.status)

        req.status = JoinRequestStatus.rejected.value
        req.decided_at = now_utc_ms()
        req.decided_by = actor_username
        if reason is not None:
            req.reason = reason
        await self.db.flush()

        _ = await self._notifications.clear_actionable(
            "group_join_request", pending_action_id=req.id
        )

        group_result = await self.db.execute(select(Group).where(Group.id == group_id))
        group = group_result.scalar_one()
        await self._notifications.notify_for_event(
            NotificationEvent(
                type="group.join_response",
                recipients=[req.username],
                data={
                    "groupId": group_id,
                    "groupName": group.name,
                    "outcome": "rejected",
                    "actorUsername": actor_username,
                    "reason": reason,
                },
            )
        )
        return req
