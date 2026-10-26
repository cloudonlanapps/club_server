from typing import ClassVar, Self, TypedDict
from pydantic import ConfigDict, Field, model_validator

from ..age_eligibility import Age, decode_age
from ..db.models.group import Group
from ..db.models.user import Gender
from ..genders import reject_repeated_genders
from .common import CamelCaseModel


class GroupCreate(CamelCaseModel):
    """Schema for creating a group."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool = False
    genders: list[Gender] | None = None
    semi_auto: bool = False

    @model_validator(mode="after")
    def _genders(self) -> Self:
        return reject_repeated_genders(self)


class GroupUpdate(CamelCaseModel):
    """Schema for updating a group."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool | None = None
    genders: list[Gender] | None = None
    semi_auto: bool | None = None

    @model_validator(mode="after")
    def _genders(self) -> Self:
        return reject_repeated_genders(self)


class GroupMemberInfo(CamelCaseModel):
    """Member information within a group."""

    membername: str
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    # False for a semi-auto member who no longer meets the group's criteria
    # (groups R82); worked out when the row is read.
    eligible: bool = True


class EligibleUserInfo(CamelCaseModel):
    """Smallest user shape returned by the eligibility query."""

    username: str
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None


class AgeBandFields(TypedDict):
    """The age-band response fields ``age_band_fields`` produces."""

    min_age: Age | None
    max_age: Age | None
    strict_age: bool
    dob_on_or_after_utc: int | None
    dob_on_or_before_utc: int | None
    eligibility_reference_day_utc: int


def age_band_fields(group: Group) -> AgeBandFields:
    """A group's age band and the window it comes to today, as response fields."""
    window = group.eligibility_window
    return {
        "min_age": decode_age(group.min_age),
        "max_age": decode_age(group.max_age),
        "strict_age": bool(group.strict_age),
        "dob_on_or_after_utc": window.dob_on_or_after_utc,
        "dob_on_or_before_utc": window.dob_on_or_before_utc,
        "eligibility_reference_day_utc": window.reference_day_utc,
    }


class GroupResponse(CamelCaseModel):
    """Schema for group response."""

    id: int
    name: str
    description: str | None
    kind: str
    # The age band (eligibility R1), and what it comes to today (R12): the
    # window of birth dates, both ends inclusive, and the day it is counted on.
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool = False
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    eligibility_reference_day_utc: int
    genders: list[str] = []
    member_count: int
    # Stored members who no longer meet the group's criteria (groups R83).
    ineligible_member_count: int = 0
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
        ineligible_member_count: int = 0,
    ) -> "GroupResponse":
        """Create response from Group model."""
        return cls(
            id=group.id,
            name=group.name,
            description=group.description,
            kind=group.kind,
            **age_band_fields(group),
            genders=group.gender_list,
            member_count=member_count,
            ineligible_member_count=ineligible_member_count,
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
    # The age band (eligibility R1), and what it comes to today (R12): the
    # window of birth dates, both ends inclusive, and the day it is counted on.
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool = False
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    eligibility_reference_day_utc: int
    genders: list[str] = []
    members: list[GroupMemberInfo]
    # Stored members who no longer meet the group's criteria (groups R83).
    ineligible_member_count: int = 0
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
