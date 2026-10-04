from typing import ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


T = TypeVar("T")


class CamelCaseModel(BaseModel):
    """Base model that outputs camelCase JSON keys."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        # Reject fields the schema does not declare (#325). Pydantic's default
        # is to discard them, so a client sending a misspelled or retired
        # field got 200 with the operation silently not performed.
        extra="forbid",
    )


class ErrorDetail(BaseModel):
    """Error detail model."""

    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    """Standard error response format."""

    error: ErrorDetail


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""

    items: list[T]
    total: int
    offset: int
    limit: int


class CapabilitiesResponse(CamelCaseModel):
    """What this deployment can do (#339).

    An object rather than a bare flag so later capabilities can join it
    without a new endpoint. Describes the deployment, not the caller —
    every client gets the same answer.
    """

    credit_system: bool
    evaluations: bool = False
    event_marketing: bool = False
    # Whether a new user uploads an identity document before review (#428).
    identity_verification: bool = True


class SystemStatusResponse(CamelCaseModel):
    """Schema for system status check response."""

    has_any_users: bool


class UnreadCountResponse(CamelCaseModel):
    """Schema for unread notification count response."""

    count: int


class CountResponse(CamelCaseModel):
    """Generic count response."""

    count: int


FieldValue = str | int | bool | list[str] | None


class UserRoles(BaseModel):
    """Model for parsing user roles JSON."""

    roles: list[str] = []


class Address(CamelCaseModel):
    """Model for parsing user address JSON."""

    addr_line1: str | None = None
    addr_line2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None


class FieldChange(BaseModel):
    """Model for tracking a single field change."""

    old: FieldValue
    new: FieldValue


class ChangeLog(BaseModel):
    """Model for tracking multiple field changes."""

    changes: dict[str, FieldChange]

    def __init__(self, **data: FieldChange) -> None:
        """Initialize with field changes as kwargs."""
        super().__init__(changes=data)

    def add(self, field: str, old: FieldValue, new: FieldValue) -> None:
        """Add a field change."""
        self.changes[field] = FieldChange(old=old, new=new)

    def __bool__(self) -> bool:
        """Return True if there are any changes."""
        return bool(self.changes)

    def to_audit_dict(self) -> dict[str, dict[str, FieldValue]]:
        """Convert to dict format for audit logging."""
        return {k: {"old": v.old, "new": v.new} for k, v in self.changes.items()}
