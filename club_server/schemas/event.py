from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import ConfigDict, Field, model_validator

from ..age_eligibility import Age, EligibilityWindow, decode_age
from ..db.models.event import Event
from ..db.models.event_schedule import EventSchedule
from .common import CamelCaseModel, to_camel
from .event_marketing import BasicMarketingFields
from .user import PublicProfileResponse

if TYPE_CHECKING:
    from ..services.conflict_gates import Finding

VisibilityT = Literal["public", "private"]


def _reject_until_time_utc(data: Any) -> Any:
    """Reject untilTimeUtc on event create/update (issue #105).

    The cutoff is written only by terminate, extend, split and cancel, which
    record a reason and handle occurrence side-effects.
    """
    if isinstance(data, dict) and ("until_time_utc" in data or "untilTimeUtc" in data):
        raise ValueError("untilTimeUtc may only be set via the lifecycle endpoints")
    return data


def _event_service_kwargs(model: CamelCaseModel) -> dict[str, object]:
    """Map EventCreate/EventUpdate fields to the EventService kwarg names.

    Renames ``type``→``event_type``, ``start_time_utc``→``start_time`` and
    ``end_time_utc``→``end_time``; every other field passes through unchanged.
    """
    rename = {
        "type": "event_type",
        "start_time_utc": "start_time",
        "end_time_utc": "end_time",
    }
    return {
        rename.get(name, name): getattr(model, name)
        for name in type(model).model_fields
        if name != "version"
    }


class Session(CamelCaseModel):
    """One entry in an event's per-occurrence timetable.

    `period_minutes` is the duration of this slot. The ordered list of
    sessions describes the timetable inside each occurrence's start/end
    window; the sum of all periods must equal that window in minutes.
    """

    name: str = Field(..., min_length=1, max_length=200)
    period_minutes: int = Field(..., ge=1)


class EventCreate(BasicMarketingFields):
    """Schema for creating an event."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    type: str = Field(..., description="oneOff, programme, or camp")
    visibility: VisibilityT = "private"
    venue_id: int
    organizer_name: str | None = None
    coach_names: list[str] | None = None
    start_time_utc: int
    end_time_utc: int
    rrule: str | None = None
    gender: str | None = None
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool = False
    is_featured: bool = False
    gallery_uris: list[str] | None = None
    sessions: list[Session] | None = None

    _reject_until = model_validator(mode="before")(_reject_until_time_utc)

    def to_service_kwargs(self) -> dict[str, object]:
        return _event_service_kwargs(self)


class EventUpdate(BasicMarketingFields):
    """Schema for updating a camp's or one-off's metadata (#232, #248).

    The schedule fields (`startTimeUtc`, `endTimeUtc`, `rrule`, `venueId`)
    move through `POST /v1/events/by_id/{event_id}/reschedule`;
    `extra="forbid"` rejects them here with 422 rather than dropping them.
    `sessions` is accepted here too, at any time, because correcting a
    timetable moves nothing (camp R105, one-off R22a).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    version: int = Field(..., ge=1, description="The event version the client last saw")
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    visibility: VisibilityT | None = None
    organizer_name: str | None = None
    coach_names: list[str] | None = None
    gender: str | None = None
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool | None = None
    is_featured: bool | None = None
    gallery_uris: list[str] | None = None
    sessions: list[Session] | None = None

    _reject_until = model_validator(mode="before")(_reject_until_time_utc)

    def to_service_kwargs(self) -> dict[str, object]:
        return _event_service_kwargs(self)


class EventResponse(CamelCaseModel):
    """An event, with its **current** schedule's timetable fields.

    ``untilTimeUtc`` is the cutoff — the current schedule's ``effectiveUntil``
    (lifecycle L1). The full timeline is ``GET .../schedules``.
    """

    id: int
    title: str
    description: str | None
    type: str
    visibility: str
    venue_id: int
    organizer_name: str | None
    coach_names: list[str] | None
    # The consenting coaches as public profiles (#299, public R15); filled by
    # the event reads, None on responses that do not resolve it.
    coaches: list[PublicProfileResponse] | None = None
    rrule: str | None
    start_time_utc: int
    end_time_utc: int
    until_time_utc: int | None
    gender: str | None = None
    # The age band (eligibility R1), and what it comes to on the event's
    # reference day (R12): the window of birth dates, both ends inclusive.
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool = False
    dob_on_or_after_utc: int | None = None
    dob_on_or_before_utc: int | None = None
    eligibility_reference_day_utc: int
    is_featured: bool = False
    gallery_uris: list[str] | None = None
    sessions: list[Session] | None = None
    short_description: str | None = None
    stamp: str | None = None
    highlights: list[str] | None = None
    includes: list[str] | None = None
    created_at_utc: int
    updated_at_utc: int
    deleted_at_utc: int | None = None
    version: int
    updated_by: str | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
        from_attributes=True, populate_by_name=True
    )

    @classmethod
    def from_model(cls, event: Event, window: EligibilityWindow) -> "EventResponse":
        """Create response from Event model and its window of birth dates."""
        import json

        current = event.current_schedule
        return cls(
            id=event.id,
            title=event.title,
            description=event.description,
            type=event.type,
            visibility=event.visibility,
            venue_id=current.venue_id,
            organizer_name=current.organizer_name,
            coach_names=current.coach_names_list or None,
            rrule=current.rrule,
            start_time_utc=current.start_time,
            end_time_utc=current.end_time,
            until_time_utc=current.effective_until,
            gender=event.gender,
            min_age=decode_age(event.min_age),
            max_age=decode_age(event.max_age),
            strict_age=bool(event.strict_age),
            dob_on_or_after_utc=window.dob_on_or_after_utc,
            dob_on_or_before_utc=window.dob_on_or_before_utc,
            eligibility_reference_day_utc=window.reference_day_utc,
            is_featured=bool(event.is_featured),
            gallery_uris=json.loads(event.gallery_uris) if event.gallery_uris else None,
            sessions=[Session(**s) for s in json.loads(current.sessions)]
            if current.sessions
            else None,
            short_description=event.short_description,
            stamp=event.stamp,
            highlights=json.loads(event.highlights) if event.highlights else None,
            includes=json.loads(event.includes) if event.includes else None,
            created_at_utc=event.created_at,
            updated_at_utc=event.updated_at,
            deleted_at_utc=event.deleted_at,
            version=event.version,
            updated_by=event.updated_by,
        )


class EventScheduleResponse(CamelCaseModel):
    """One period of an event's timetable (``event_schedule_model.md``)."""

    id: int
    event_id: int
    effective_from_utc: int
    effective_until_utc: int | None
    start_time_utc: int
    end_time_utc: int
    rrule: str | None
    venue_id: int
    organizer_name: str | None
    coach_names: list[str] | None
    sessions: list[Session] | None

    @classmethod
    def from_model(cls, schedule: EventSchedule) -> "EventScheduleResponse":
        """Create response from an EventSchedule row."""
        import json

        return cls(
            id=schedule.id,
            event_id=schedule.event_id,
            effective_from_utc=schedule.effective_from,
            effective_until_utc=schedule.effective_until,
            start_time_utc=schedule.start_time,
            end_time_utc=schedule.end_time,
            rrule=schedule.rrule,
            venue_id=schedule.venue_id,
            organizer_name=schedule.organizer_name,
            coach_names=schedule.coach_names_list or None,
            sessions=[Session(**s) for s in json.loads(schedule.sessions)]
            if schedule.sessions
            else None,
        )


class ConflictCheckRequest(CamelCaseModel):
    """Request body for ``POST /v1/events/check-conflict`` (any type, camp R31).

    Carries the full target schedule so the server can compare it against
    everything else through the venue, organizer and coach gates.
    """

    type: str = Field(..., description="oneOff, programme, or camp")
    venue_id: int
    start_time_utc: int
    end_time_utc: int
    rrule: str | None = None
    until_time_utc: int | None = None
    organizer_name: str | None = None
    coach_names: list[str] | None = None
    exclude_event_id: int | None = None


CampConflictCheckRequest = ConflictCheckRequest


class CheckUserConflictsRequest(CamelCaseModel):
    """Request body for ``POST /v1/events/by_id/{id}/check-user-conflicts``."""

    usernames: list[str] = Field(default_factory=list)


class OccurrencePairResponse(CamelCaseModel):
    """A pair of overlapping occurrences in milliseconds since epoch."""

    target_start_utc: int
    target_end_utc: int
    other_start_utc: int
    other_end_utc: int

    @classmethod
    def from_tuple(cls, pair: tuple[int, int, int, int]) -> "OccurrencePairResponse":
        return cls(
            target_start_utc=pair[0],
            target_end_utc=pair[1],
            other_start_utc=pair[2],
            other_end_utc=pair[3],
        )


class EventConflictItemResponse(CamelCaseModel):
    """A conflicting event with its overlap pairs against the target."""

    event_id: int
    event_title: str
    event_type: str
    occurrences: list[OccurrencePairResponse]

    @classmethod
    def from_finding(cls, item: "Finding") -> "EventConflictItemResponse":
        return cls(
            event_id=item.event_id,
            event_title=item.event_title,
            event_type=item.event_type,
            occurrences=[
                OccurrencePairResponse.from_tuple(p) for p in item.occurrences
            ],
        )


class UserConflictItemResponse(CamelCaseModel):
    """A user with the events whose occurrences overlap the target."""

    username: str
    events: list[EventConflictItemResponse]


class ConflictReportResponse(CamelCaseModel):
    """Conflict-check report: the venue, organizer and coach gates' findings."""

    venue_conflicts: list[EventConflictItemResponse] = Field(default_factory=list)
    organizer_conflicts: list[EventConflictItemResponse] = Field(default_factory=list)
    coach_conflicts: list[EventConflictItemResponse] = Field(default_factory=list)


CampConflictReport = ConflictReportResponse


class UserConflictReport(CamelCaseModel):
    """Response body for ``POST /v1/events/by_id/{id}/check-user-conflicts``."""

    user_conflicts: list[UserConflictItemResponse] = Field(default_factory=list)


class EventCorrectionRequest(BasicMarketingFields):
    """Correct a programme's identity, eligibility and presentation (R21, R22a, R25a).

    Every field here lives on the programme and has one value at a time. No
    scheduling or staffing field is accepted (R22, R21a): those are a split.
    The exception is ``sessions``, which corrects one schedule's timetable —
    the one ``schedule_id`` names, or the latest (R22c–R22e).
    """

    version: int = Field(..., ge=1, description="The event version the client last saw")
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    visibility: VisibilityT | None = None
    gender: str | None = None
    min_age: Age | None = None
    max_age: Age | None = None
    strict_age: bool | None = None
    is_featured: bool | None = None
    gallery_uris: list[str] | None = None
    sessions: list[Session] | None = None
    schedule_id: int | None = None

    @model_validator(mode="after")
    def _schedule_id_needs_sessions(self) -> "EventCorrectionRequest":
        """R22d: ``scheduleId`` says whose timetable is corrected, so it needs one."""
        if "schedule_id" in self.model_fields_set and (
            "sessions" not in self.model_fields_set
        ):
            raise ValueError("scheduleId is only accepted with sessions")
        return self


class EventCancelRequest(CamelCaseModel):
    """Cancel a camp series from an occurrence start (camp R5)."""

    reason: str = Field(..., min_length=1, max_length=500)
    effective_date_time_utc: int = Field(...)


class EventUpdateFutureRequest(CamelCaseModel):
    """Split a programme: close the current schedule at the cutoff and open
    the next with these terms (R23–R25a).

    Every field is scheduling or staffing; identity fields are corrected,
    never split (R25b). At least one term must change.
    """

    version: int = Field(..., ge=1, description="The event version the client last saw")
    effective_date_time_utc: int
    venue_id: int | None = None
    organizer_name: str | None = None
    coach_names: list[str] | None = None
    start_time_utc: int | None = None
    end_time_utc: int | None = None
    rrule: str | None = None
    sessions: list[Session] | None = None

    @model_validator(mode="after")
    def _require_one_schedule_field(self) -> "EventUpdateFutureRequest":
        if not (self.model_fields_set - {"effective_date_time_utc", "version"}):
            raise ValueError(
                "At least one of venueId, organizerName, coachNames, startTimeUtc, "
                "endTimeUtc, rrule, sessions is required"
            )
        return self


class EventRescheduleRequest(CamelCaseModel):
    """Reschedule a camp or one-off in place (issue #230, #248).

    Carries the schedule fields locked out of the generic PATCH by #112.
    Only the provided fields change; the rest keep their current values.

    ``sessions`` is part of the schedule (#248): when present it replaces the
    stored per-occurrence timetable atomically with the rest of the change and
    is validated against the new window; send ``null`` to clear the timetable.
    When omitted, the existing timetable is re-validated against the new window.

    ``version`` is required, as on update, correction and split (lifecycle
    L22a, #434): a reschedule rewrites the timetable those edits read.
    """

    version: int = Field(..., ge=1, description="The event version the client last saw")
    start_time_utc: int | None = None
    end_time_utc: int | None = None
    rrule: str | None = None
    venue_id: int | None = None
    sessions: list[Session] | None = None
    reset_overrides: bool = False

    @model_validator(mode="after")
    def _require_one_schedule_field(self) -> "EventRescheduleRequest":
        if (
            self.start_time_utc is None
            and self.end_time_utc is None
            and self.rrule is None
            and self.venue_id is None
            and "sessions" not in self.model_fields_set
        ):
            raise ValueError(
                "At least one of startTimeUtc, endTimeUtc, rrule, venueId, "
                "sessions is required"
            )
        return self


class EventTerminateRequest(CamelCaseModel):
    """End a programme at a cutoff (programme R1–R4).

    ``cutoff_time_utc`` must be an occurrence start of the current schedule,
    at least 30 minutes ahead. There is no credit disposition here (R10a).
    """

    reason: str = Field(..., min_length=1, max_length=500)
    cutoff_time_utc: int


class EventExtendRequest(CamelCaseModel):
    """Move a terminated programme's cutoff, later or earlier (R6, R7)."""

    cutoff_time_utc: int
    reason: str | None = Field(None, max_length=500)


class EventExtendIndefinitelyRequest(CamelCaseModel):
    """Remove a terminated programme's cutoff (R8)."""

    reason: str | None = Field(None, max_length=500)


class EventDropRequest(CamelCaseModel):
    """Call off a one-off (one-off R3). Takes no effective time (R4).

    ``version`` is the version of the one-off's single **occurrence**, which
    is what a drop changes (lifecycle L23a), not the event's.
    """

    version: int = Field(
        ..., ge=1, description="The occurrence version the client last saw"
    )
    reason: str = Field(..., min_length=1, max_length=500)


class EventReinstateRequest(CamelCaseModel):
    """Put a dropped one-off back on (one-off R6), at its occurrence's version."""

    version: int = Field(
        ..., ge=1, description="The occurrence version the client last saw"
    )
