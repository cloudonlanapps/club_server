from typing import ClassVar

from pydantic import ConfigDict, Field

from ..db.models.attendance import AttendanceRecord
from .common import CamelCaseModel


class AttendanceRecordResponse(CamelCaseModel):
    """Schema for attendance record response."""

    event_id: int
    occurrence_time_utc: int
    membername: str
    status: str
    notes: str | None
    previous_status: str | None
    leave_reason: str | None
    recorded_at_utc: int

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(cls, record: AttendanceRecord) -> "AttendanceRecordResponse":
        """Create response from AttendanceRecord model."""
        return cls(
            event_id=record.event_id,
            occurrence_time_utc=record.occurrence_time_utc,
            membername=record.membername,
            status=record.status,
            notes=record.notes,
            previous_status=record.previous_status,
            leave_reason=record.leave_reason,
            recorded_at_utc=record.recorded_at,
        )


class MarkAttendanceRequest(CamelCaseModel):
    """Schema for marking attendance."""

    membername: str
    status: str
    notes: str | None = None


class BulkMarkAttendanceRequest(CamelCaseModel):
    """Schema for bulk marking attendance."""

    records: list[MarkAttendanceRequest] = Field(..., min_length=1)


class DeclareLeaveRequest(CamelCaseModel):
    """Schema for declaring leave."""

    reason: str | None = None


class ApproveLeaveRequest(CamelCaseModel):
    """Schema for approving leave requests."""

    membernames: list[str] = Field(..., min_length=1)


class RejectLeaveRequest(CamelCaseModel):
    """Schema for rejecting leave requests."""

    membernames: list[str] = Field(..., min_length=1)
    reason: str | None = None


class MarkedAttendanceEntry(CamelCaseModel):
    """One member whose attendance was recorded."""

    membername: str
    status: str


class RefusedAttendanceEntry(CamelCaseModel):
    """One member whose attendance was not recorded, and why."""

    membername: str
    code: str
    message: str


class TrialEndedAttendanceEntry(CamelCaseModel):
    """One member whose trial this request ended (credit R52)."""

    membername: str


class BulkMarkAttendanceResponse(CamelCaseModel):
    """Per-member outcome of a bulk mark (#294, R41b).

    Members are settled independently: one member's lapsed credit never
    prevents another's attendance from being recorded. Deployments that do
    not run on credits get the same shape with always-empty ``refused`` and
    ``trialEnded`` lists, so the response never varies with configuration
    (R97).

    ``trial_ended`` lists the members whose mark spent the last of their
    trial credit, which removed them from the programme (#447). Each also
    appears in ``marked``.
    """

    marked: list[MarkedAttendanceEntry]
    refused: list[RefusedAttendanceEntry]
    trial_ended: list[TrialEndedAttendanceEntry]
