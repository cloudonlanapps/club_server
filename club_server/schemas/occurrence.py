from pydantic import Field

from .common import CamelCaseModel


class OccurrenceRescheduleRequest(CamelCaseModel):
    """Schema for rescheduling an occurrence.

    A Camp day is described by when it starts and how long it lasts. At least
    one field must be set; the service rejects an all-null body (#113).
    """

    version: int = Field(
        ..., ge=1, description="The occurrence version the client last saw"
    )
    new_start_time_utc: int | None = None
    new_duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    new_venue_id: int | None = None


class OccurrenceCancelRequest(CamelCaseModel):
    """Schema for cancelling an occurrence."""

    version: int = Field(
        ..., ge=1, description="The occurrence version the client last saw"
    )
    reason: str = Field(..., min_length=1, max_length=500)


class OccurrenceVersionRequest(CamelCaseModel):
    """A change to an occurrence that carries nothing but its version (#430)."""

    version: int = Field(
        ..., ge=1, description="The occurrence version the client last saw"
    )


class OccurrenceResponse(CamelCaseModel):
    """Schema for occurrence response."""

    event_id: int
    event_title: str
    event_type: str
    occurrence_time_utc: int
    start_time_utc: int
    end_time_utc: int
    venue_id: int | None
    venue_name: str | None
    organizer_name: str | None
    organizer_display_name: str | None
    status: str
    is_rescheduled: bool
    cancel_reason: str | None
    attendance_status: str | None = None
    # Optimistic locking (#430, lifecycle L23): 1 until the occurrence is
    # first changed; who changed it last, and when, once it has been.
    version: int
    updated_at: int | None
    updated_by: str | None


class OccurrenceListFilter(CamelCaseModel):
    """Schema for occurrence list filters."""

    from_time_utc: int
    to_time_utc: int
    filter_type: str | None = None
    username: str | None = None
    event_type: str | None = None
