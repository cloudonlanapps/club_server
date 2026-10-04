from typing import ClassVar
from pydantic import ConfigDict, Field

from ..db.models.group import Group
from .common import CamelCaseModel


class GroupCreate(CamelCaseModel):
    """Schema for creating a group."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    gender: str | None = None
    semi_auto: bool = False


class GroupUpdate(CamelCaseModel):
    """Schema for updating a group."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    gender: str | None = None
    semi_auto: bool | None = None


class GroupMemberInfo(CamelCaseModel):
    """Member information within a group."""

    membername: str
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None


class EligibleUserInfo(CamelCaseModel):
    """Smallest user shape returned by the eligibility query."""

    username: str
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None


class GroupResponse(CamelCaseModel):
    """Schema for group response."""

    id: int
    name: str
    description: str | None
    kind: str
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    gender: str | None = None
    member_count: int
    created_at_utc: int
    deleted_at_utc: int | None = None
    requested: bool = False

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(
        cls,
        group: Group,
        member_count: int = 0,
        requested: bool = False,
    ) -> "GroupResponse":
        """Create response from Group model."""
        return cls(
            id=group.id,
            name=group.name,
            description=group.description,
            kind=group.kind,
            dob_on_or_after_utc=group.dob_on_or_after_utc,
            dob_on_or_before_utc=group.dob_on_or_before_utc,
            gender=group.gender,
            member_count=member_count,
            created_at_utc=group.created_at,
            deleted_at_utc=group.deleted_at,
            requested=requested,
        )


class GroupDetailResponse(CamelCaseModel):
    """Schema for detailed group response with members."""

    id: int
    name: str
    description: str | None
    kind: str
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    gender: str | None = None
    members: list[GroupMemberInfo]
    created_at_utc: int
    deleted_at_utc: int | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )


class BulkMembersAdd(CamelCaseModel):
    """Schema for adding multiple members to a group."""

    membernames: list[str] = Field(..., min_length=1)


class BulkMembersResult(CamelCaseModel):
    """Result of bulk member add operation."""

    added: list[str]
    already_members: list[str]
    not_found: list[str]
    not_eligible: list[str] = Field(default_factory=list)
