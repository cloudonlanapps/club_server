from typing import ClassVar, Literal
from sqlalchemy import inspect as sa_inspect
from pydantic import ConfigDict, EmailStr, Field

from ..db.models.user import Gender, Role, User, UserStatus
from ..utils import generate_public_id, get_display_name
from .common import Address, CamelCaseModel, UserRoles
from .media import MediaRef


class UserCreate(CamelCaseModel):
    """Schema for creating a new user."""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    email: EmailStr | None = None
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    nickname: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=20)
    date_of_birth_utc: int | None = None
    gender: Gender | None = None
    address: Address | None = None


class UserAdminCreate(CamelCaseModel):
    """Schema for admin-created user."""

    username: str = Field(..., min_length=3, max_length=50)
    password_hash: str = Field(..., description="Pre-hashed password")
    email: EmailStr | None = None
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    nickname: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=20)
    date_of_birth_utc: int | None = None
    use_name_publicly: bool | None = None
    bio: str | None = None
    medical_info: str | None = None
    emergency_contact: str | None = None
    achievements: str | None = None
    gender: Gender | None = None
    address: Address | None = None
    # An admin creates active users only (#522): any other value → 422.
    status: Literal["active"] = "active"
    # A guest coach (#332): curated as a guest and published on the admin's
    # authority. Handled by the router, not forwarded to the service.
    is_guest: bool = False

    def to_service_kwargs(self) -> dict[str, object]:
        """Build kwargs for ``UserService.create_user``.

        All fields are forwarded (admin-create applies schema defaults rather
        than the UNSET semantics of ``UserUpdate``). Renames
        ``date_of_birth_utc`` → ``date_of_birth``, serializes ``address`` to
        its JSON-string column form, lowers ``gender`` to its enum value, and
        passes ``status`` as ``UserStatus.active``, the only value accepted.
        """
        rename = {"date_of_birth_utc": "date_of_birth"}
        kwargs: dict[str, object] = {}
        for name in type(self).model_fields:
            if name == "is_guest":
                continue
            value = getattr(self, name)
            if name == "gender":
                value = value.value if value else None
            elif name == "address":
                value = value.model_dump_json() if value else None
            elif name == "status":
                value = UserStatus.active
            kwargs[rename.get(name, name)] = value
        return kwargs


class UserUpdate(CamelCaseModel):
    """Schema for updating a user."""

    email: EmailStr | None = None
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    nickname: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=20)
    use_name_publicly: bool | None = None
    bio: str | None = None
    date_of_birth_utc: int | None = None
    medical_info: str | None = None
    emergency_contact: str | None = None
    achievements: str | None = None
    is_public_profile: bool | None = None
    gender: Gender | None = None
    address: Address | None = None

    def to_service_kwargs(self) -> dict[str, object]:
        """Build kwargs for ``UserService.update_user``.

        Only explicitly-set fields are included so the service's ``UNSET``
        sentinel can distinguish "leave unchanged" from "clear to null".
        Renames ``date_of_birth_utc`` → ``date_of_birth`` and serializes
        ``address`` to the JSON-string column format the service expects.
        """
        rename = {"date_of_birth_utc": "date_of_birth"}
        kwargs: dict[str, object] = {}
        for name in self.model_fields_set:
            if name == "address":
                kwargs["address"] = (
                    self.address.model_dump_json() if self.address else None
                )
            else:
                kwargs[rename.get(name, name)] = getattr(self, name)
        return kwargs


class RoleAssign(CamelCaseModel):
    """Schema for assigning a role."""

    role: Role


class ReconsiderRequest(CamelCaseModel):
    """Schema for ``POST /v1/users/by_id/{username}/reconsider`` (admin)."""

    reason: str = Field(..., min_length=1, max_length=500)


class ReapplyRequest(CamelCaseModel):
    """Schema for ``PATCH /v1/users/by_id/{username}/reapply`` (self).

    Only the registration-field subset is accepted. Pydantic ignores
    unknown keys by default, so other ``UserUpdate`` fields submitted
    through this endpoint are silently dropped.
    """

    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    date_of_birth_utc: int | None = None
    gender: Gender | None = None
    phone: str | None = Field(None, max_length=20)
    email: EmailStr | None = None

    def to_update_fields(self) -> dict[str, object]:
        """Build the field dict consumed by ``UserReviewService.reapply``.

        Only explicitly-set fields are included; renames
        ``date_of_birth_utc`` → ``date_of_birth`` and converts ``gender`` to
        its string value before persisting via ``setattr`` on the user row.
        """
        rename = {"date_of_birth_utc": "date_of_birth"}
        fields: dict[str, object] = {}
        for name in self.model_fields_set:
            value = getattr(self, name)
            if name == "gender" and value is not None:
                value = value.value
            fields[rename.get(name, name)] = value
        return fields


class ResolutionReasonBody(CamelCaseModel):
    """Optional body for ``approve``/``block`` carrying the closing
    note saved to the active ``user_review_requests`` row, if any."""

    resolution_reason: str | None = Field(None, max_length=500)


class UserInfoResponse(CamelCaseModel):
    """Public user info response schema (UserInfo equivalent)."""

    username: str
    public_id: str
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    use_name_publicly: bool = False
    bio: str | None = None
    status: str
    is_super_admin: bool = False
    is_public_profile: bool = False
    is_guest: bool = False
    roles: list[str] = []
    deleted_at_utc: int | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(cls, user: User) -> "UserInfoResponse":
        """Create response from User model."""
        roles_data = (
            UserRoles.model_validate_json(user.roles) if user.roles else UserRoles()
        )
        return cls(
            username=user.username,
            public_id=generate_public_id(user.username),
            first_name=user.first_name,
            middle_name=user.middle_name,
            last_name=user.last_name,
            nickname=user.nickname,
            use_name_publicly=bool(user.use_name_publicly),
            bio=user.bio,
            status=user.status,
            is_super_admin=bool(user.is_super_admin),
            is_public_profile=bool(user.is_public_profile),
            is_guest=is_guest_of(user),
            roles=roles_data.roles,
            deleted_at_utc=user.deleted_at,
        )


class UserCountResponse(CamelCaseModel):
    """Per-status user counts (#308).

    ``by_status`` always carries every :class:`UserStatus`, zero included, so
    a caller can render a stable set of buckets. ``total`` is the sum of all
    of them — the ``registered`` bucket included, unlike ``GET /users``, which
    omits it unless asked for it. Soft-deleted users and super admins are
    counted nowhere, matching the listing endpoints (#103).
    """

    by_status: dict[UserStatus, int]
    total: int


def is_guest_of(user: User) -> bool:
    """Whether the coach is curated as a guest on the public staff page (#332).

    A user built in this request (registration, admin create) has never had
    the relationship loaded; touching it would lazy-load outside the async
    context. Such a user has no listing row yet, so the answer is False.
    """
    if "staff_listing" in sa_inspect(user).unloaded:
        return False
    row = user.staff_listing
    return bool(row.is_guest) if row is not None else False


class PublicProfileResponse(CamelCaseModel):
    """Privacy-safe public profile (no username), for ``/public`` endpoints.

    Mirrors the SDK's ``PublicProfile``. Served to anonymous website visitors
    and to logged-in members opening a coach profile. ``display_name`` is the
    privacy-aware name (``get_display_name``); ``avatar`` is set only when
    the user's latest avatar media is publicly viewable.
    """

    public_id: str
    display_name: str
    bio: str | None = None
    achievements: str | None = None
    avatar: MediaRef | None = None
    is_guest: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_user(
        cls, user: User, *, avatar: MediaRef | None, is_guest: bool = False
    ) -> "PublicProfileResponse":
        """Build from a User + the pre-resolved public avatar descriptor (or None)."""
        return cls(
            public_id=generate_public_id(user.username),
            display_name=get_display_name(user),
            bio=user.bio,
            achievements=user.achievements,
            avatar=avatar,
            is_guest=is_guest,
        )


class UserPrivateResponse(CamelCaseModel):
    """Private user response schema (UserPrivate equivalent)."""

    username: str
    public_id: str
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    use_name_publicly: bool = False
    is_public_profile: bool = False
    email: str | None = None
    phone: str | None = None
    bio: str | None = None
    date_of_birth_utc: int | None = None
    medical_info: str | None = None
    emergency_contact: str | None = None
    achievements: str | None = None
    is_guest: bool = False
    gender: str | None = None
    address: Address | None = None
    status: str
    is_super_admin: bool = False
    roles: list[str] = []
    last_login_at_utc: int | None = None
    created_at_utc: int
    deleted_at_utc: int | None = None
    admin_review_note: str | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(
        cls,
        user: User,
        admin_review_note: str | None = None,
    ) -> "UserPrivateResponse":
        """Create response from User model.

        ``admin_review_note`` surfaces the active ``user_review_requests``
        row's ``reason`` for read endpoints (see #123); write endpoints
        leave it ``None``.
        """
        roles_data = (
            UserRoles.model_validate_json(user.roles) if user.roles else UserRoles()
        )
        return cls(
            username=user.username,
            public_id=generate_public_id(user.username),
            first_name=user.first_name,
            middle_name=user.middle_name,
            last_name=user.last_name,
            nickname=user.nickname,
            use_name_publicly=bool(user.use_name_publicly),
            is_public_profile=bool(user.is_public_profile),
            email=user.email,
            phone=user.phone,
            bio=user.bio,
            date_of_birth_utc=user.date_of_birth,
            medical_info=user.medical_info,
            emergency_contact=user.emergency_contact,
            achievements=user.achievements,
            is_guest=is_guest_of(user),
            gender=user.gender,
            address=Address.model_validate_json(user.address) if user.address else None,
            status=user.status,
            is_super_admin=bool(user.is_super_admin),
            roles=roles_data.roles,
            last_login_at_utc=user.last_login_at,
            created_at_utc=user.created_at,
            deleted_at_utc=user.deleted_at,
            admin_review_note=admin_review_note,
        )


# Legacy aliases for backward compatibility
UserResponse = UserInfoResponse
