from typing import ClassVar
from pydantic import ConfigDict, Field

from ..db.models.enrollment import Enrollment
from .common import CamelCaseModel
from .credit import CreditDispositionRequest


class EnrollmentResponse(CamelCaseModel):
    """Schema for enrollment response."""

    id: int
    event_id: int
    membername: str
    status: str
    is_trial: bool
    previous_status: str | None
    withdrawal_reason: str | None
    enrolled_at_utc: int | None
    withdrawn_at_utc: int | None
    created_at_utc: int
    updated_at_utc: int | None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(cls, enrollment: Enrollment) -> "EnrollmentResponse":
        """Create response from Enrollment model."""
        return cls(
            id=enrollment.id,
            event_id=enrollment.event_id,
            membername=enrollment.membername,
            status=enrollment.status,
            is_trial=bool(enrollment.is_trial),
            previous_status=enrollment.previous_status,
            withdrawal_reason=enrollment.withdrawal_reason,
            enrolled_at_utc=enrollment.enrolled_at,
            withdrawn_at_utc=enrollment.withdrawn_at,
            created_at_utc=enrollment.created_at,
            updated_at_utc=enrollment.updated_at,
        )


class EnrollmentStatusResponse(CamelCaseModel):
    """Schema for simple enrollment status response."""

    status: str | None = None


class EnrollmentListResponse(CamelCaseModel):
    """Schema for enrollment list response.

    ``enrollments`` is the legacy ``membername -> status`` map. ``records``
    carries the full enrollment objects (including ``enrolledAtUtc`` /
    ``withdrawnAtUtc``) so admin clients can compute per-occurrence
    eligibility locally — see ``enrollment_covers_occurrence``.
    """

    enrollments: dict[str, str]
    records: list[EnrollmentResponse] = Field(default_factory=list)


class InviteRequest(CamelCaseModel):
    """Schema for inviting users to an event."""

    membernames: list[str]


class AssignRequest(CamelCaseModel):
    """Schema for assigning users to an event."""

    membernames: list[str]


class AssignTrialRequest(CamelCaseModel):
    """Schema for assigning a trial to a user."""

    membername: str


class ApproveRequest(CamelCaseModel):
    """Schema for approving enrollment requests."""

    membernames: list[str]


class RejectRequest(CamelCaseModel):
    """Schema for rejecting enrollment requests."""

    membernames: list[str]
    reason: str | None = None


class RemoveRequest(CamelCaseModel):
    """Schema for removing enrollments."""

    membernames: list[str]
    reason: str | None = None
    credit_disposition: CreditDispositionRequest | None = None


class ApproveWithdrawRequest(CamelCaseModel):
    """Schema for approving withdrawal requests."""

    membernames: list[str]
    credit_disposition: CreditDispositionRequest | None = None


class RejectWithdrawRequest(CamelCaseModel):
    """Schema for rejecting withdrawal requests."""

    membernames: list[str]
    reason: str | None = None


class WithdrawRequest(CamelCaseModel):
    """Schema for requesting withdrawal."""

    reason: str | None = None
