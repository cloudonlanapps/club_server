"""Events: create, read, list, correct, update, split, cancel, reschedule,
delete and restore, plus the conflict reports (#384).

Each verb is gated by ``services/event_types.py`` — the one place a type is
compared — and answered by the type's own service. The lifecycle verbs
(terminate, extend, drop, reinstate) are in ``routers/event_lifecycle.py``;
enrollment administration in ``routers/event_enrollments.py``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_current_active_user,
    get_db,
    require_admin,
    require_admin_or_coach,
    require_organizer_or_admin,
    require_super_admin,
)
from ..exceptions import (
    BeyondSchedulingHorizonException,
    CancellationLeadTimeViolatedException,
    CutoffTooSoonException,
    EffectiveTimeInPastException,
    EffectiveTimeNotSessionBoundaryException,
    EventAlreadyCancelledException,
    EventAlreadyStartedException,
    EventConflictException,
    EventNotCancelledException,
    InvalidCampRruleException,
    InvalidDateTimeException,
    InvalidOneOffRruleException,
    InvalidProgrammeRruleException,
    InvalidRruleException,
    InvalidSessionsException,
    InvalidStateException,
    OccurrenceOverridesPresentException,
    CoachNotFoundException,
    StaleVersionException,
    OrganizerNotFoundException,
    PostponeOnlyException,
    RangeTooLargeException,
    RescheduleLeadTimeViolatedException,
    ScheduleNotFoundException,
    VenueIsDeletedException,
    VenueNotFoundException,
)
from ..schemas.attendance import AttendanceRecordResponse
from ..schemas.common import PaginatedResponse
from ..schemas.event import (
    CheckUserConflictsRequest,
    ConflictCheckRequest,
    ConflictReportResponse,
    EventCancelRequest,
    EventConflictItemResponse,
    EventCorrectionRequest,
    EventCreate,
    EventRescheduleRequest,
    EventResponse,
    EventUpdate,
    EventUpdateFutureRequest,
    EventVersionRequest,
    UserConflictItemResponse,
    UserConflictReport,
)
from ..schemas.enrollment import EnrollmentListResponse
from ..schemas.group import EligibleUserInfo
from ..services.attendance import AttendanceService
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.camp import CampService
from ..services.conflict_gates import (
    ConflictGate,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
    target_for_event,
)
from ..services.enrollment import EnrollmentService
from ..services.event import EventService
from ..services.event_eligibility import event_response
from ..services.event_listing import EventListingService
from ..services.public_event import attach_public_coaches
from ..services.event_types import (
    EventVerb,
    require_reschedulable,
    require_verb,
    validate_rrule_for,
)
from ..services.oneoff import OneOffService
from ..services.programme import ProgrammeService
from ..utils import get_client_ip

router = APIRouter(prefix="/events", tags=["Events"])


# =============================================================================
# Error mapping shared by every schedule write
# =============================================================================


def stale_event_error(exc: StaleVersionException) -> HTTPException:
    """409 for a change carrying a version the event has moved past (L22b)."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "STALE_VERSION",
            "message": "The event was changed since you last loaded it",
            "version": exc.version,
            "updatedAt": exc.updated_at,
            "updatedBy": exc.updated_by,
        },
    )


def _schedule_error(exc: Exception) -> HTTPException | None:
    """The HTTP shape of an exception any schedule write may raise."""
    if isinstance(exc, OrganizerNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Organizer not found"},
        )
    if isinstance(exc, StaleVersionException):
        return stale_event_error(exc)
    if isinstance(exc, CoachNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "USER_NOT_FOUND",
                "message": f"Coach not found: {exc.username}",
            },
        )
    if isinstance(exc, VenueNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "VENUE_NOT_FOUND", "message": str(exc)},
        )
    if isinstance(exc, ScheduleNotFoundException):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SCHEDULE_NOT_FOUND", "message": str(exc)},
        )
    if isinstance(exc, InvalidCampRruleException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_RRULE_FOR_CAMP", "message": str(exc)},
        )
    if isinstance(exc, InvalidProgrammeRruleException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_RRULE_FOR_PROGRAMME", "message": str(exc)},
        )
    if isinstance(exc, InvalidOneOffRruleException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_RRULE_FOR_ONEOFF", "message": str(exc)},
        )
    if isinstance(exc, InvalidRruleException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_RRULE", "message": "Invalid RRULE string"},
        )
    if isinstance(exc, InvalidDateTimeException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DATETIME", "message": exc.message},
        )
    if isinstance(exc, BeyondSchedulingHorizonException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "BEYOND_SCHEDULING_HORIZON", "message": str(exc)},
        )
    if isinstance(exc, InvalidSessionsException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, InvalidStateException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": exc.message},
        )
    if isinstance(exc, EventConflictException):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.conflict_report.model_dump(by_alias=True),
        )
    if isinstance(exc, EffectiveTimeNotSessionBoundaryException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY",
                "message": "Effective time must match an occurrence start",
            },
        )
    if isinstance(exc, CutoffTooSoonException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "CUTOFF_TOO_SOON", "message": str(exc)},
        )
    if isinstance(exc, EffectiveTimeInPastException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "EFFECTIVE_TIME_IN_PAST",
                "message": "Cannot cancel an occurrence in the past",
            },
        )
    if isinstance(exc, CancellationLeadTimeViolatedException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANCELLATION_LEAD_TIME_VIOLATED",
                "message": "Cancellation must be at least 30 minutes before the occurrence",
            },
        )
    if isinstance(exc, RescheduleLeadTimeViolatedException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RESCHEDULE_LEAD_TIME_VIOLATED",
                "message": "Reschedule must be at least 30 minutes before the occurrence",
            },
        )
    if isinstance(exc, PostponeOnlyException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "POSTPONE_ONLY", "message": str(exc)},
        )
    if isinstance(exc, EventAlreadyCancelledException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "EVENT_ALREADY_CANCELLED",
                "message": "Event is already cancelled",
            },
        )
    if isinstance(exc, EventNotCancelledException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EVENT_NOT_CANCELLED", "message": "Event is not cancelled"},
        )
    if isinstance(exc, EventAlreadyStartedException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "EVENT_ALREADY_STARTED", "message": str(exc)},
        )
    if isinstance(exc, OccurrenceOverridesPresentException):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "OCCURRENCE_OVERRIDES_PRESENT",
                "message": str(exc),
                "occurrenceTimeUtcs": exc.occurrence_time_utcs,
            },
        )
    return None


_SCHEDULE_EXCEPTIONS = (
    OrganizerNotFoundException,
    CoachNotFoundException,
    StaleVersionException,
    VenueNotFoundException,
    ScheduleNotFoundException,
    InvalidCampRruleException,
    InvalidProgrammeRruleException,
    InvalidOneOffRruleException,
    InvalidRruleException,
    InvalidDateTimeException,
    BeyondSchedulingHorizonException,
    InvalidSessionsException,
    InvalidStateException,
    EventConflictException,
    EffectiveTimeNotSessionBoundaryException,
    CutoffTooSoonException,
    EffectiveTimeInPastException,
    CancellationLeadTimeViolatedException,
    RescheduleLeadTimeViolatedException,
    PostponeOnlyException,
    EventAlreadyCancelledException,
    EventNotCancelledException,
    EventAlreadyStartedException,
    OccurrenceOverridesPresentException,
)


def _raise_http(exc: Exception) -> None:
    mapped = _schedule_error(exc)
    if mapped is None:
        raise exc
    raise mapped from exc


# =============================================================================
# Conflict reports (any type — camp R31)
# =============================================================================


def _report_from(findings) -> ConflictReportResponse:
    def items(gate: ConflictGate) -> list[EventConflictItemResponse]:
        return [
            EventConflictItemResponse.from_finding(f)
            for f in findings
            if f.gate is gate
        ]

    return ConflictReportResponse(
        venue_conflicts=items(ConflictGate.venue),
        organizer_conflicts=items(ConflictGate.organizer),
        coach_conflicts=items(ConflictGate.coach),
    )


@router.post("/check-conflict", response_model=ConflictReportResponse)
async def check_conflict(
    data: ConflictCheckRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Report venue / organizer / coach clashes for a proposed schedule (admin/coach).

    Any event type. A malformed rule for the type returns 422 with that type's
    code. Nothing is written and nothing blocks: this is the report.
    """
    try:
        validate_rrule_for(data.type, data.rrule)
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    report = await check_conflicts(
        db,
        ScheduleTarget(
            event_type=data.type,
            venue_id=data.venue_id,
            start_time=data.start_time_utc,
            end_time=data.end_time_utc,
            rrule=data.rrule,
            effective_from=data.start_time_utc,
            effective_until=data.until_time_utc,
            organizer_name=data.organizer_name,
            coach_names=tuple(data.coach_names or ()),
            exclude_event_id=data.exclude_event_id,
        ),
        {
            ConflictGate.venue: GatePolicy.advise,
            ConflictGate.organizer: GatePolicy.advise,
            ConflictGate.coach: GatePolicy.advise,
        },
    )
    return _report_from(report.findings)


@router.post(
    "/by_id/{event_id}/check-user-conflicts", response_model=UserConflictReport
)
async def check_user_conflicts(
    event_id: int,
    data: CheckUserConflictsRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Report which of the named users are enrolled in something that clashes
    with this event (admin/coach). Any event type."""
    event = await EventService(db).get_event(event_id)
    base = target_for_event(event)
    items: list[UserConflictItemResponse] = []
    for username in data.usernames:
        report = await check_conflicts(
            db,
            ScheduleTarget(**{**base.__dict__, "membername": username}),
            {ConflictGate.member: GatePolicy.advise},
        )
        if report.findings:
            items.append(
                UserConflictItemResponse(
                    username=username,
                    events=[
                        EventConflictItemResponse.from_finding(f)
                        for f in report.findings
                    ],
                )
            )
    return UserConflictReport(user_conflicts=items)


# =============================================================================
# Reads
# =============================================================================


@router.get("", response_model=PaginatedResponse[EventResponse])
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    from_time_utc: Annotated[int | None, Query(alias="fromTimeUtc")] = None,
    to_time_utc: Annotated[int | None, Query(alias="toTimeUtc")] = None,
    event_type: Annotated[str | None, Query(alias="type")] = None,
    visibility: str | None = None,
    venue_id: Annotated[int | None, Query(alias="venueId")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List events with filters (admin/coach only)."""
    page = await EventListingService(db).list_events(
        from_time_utc=from_time_utc,
        to_time_utc=to_time_utc,
        event_type=event_type,
        visibility=visibility,
        venue_id=venue_id,
        offset=offset,
        limit=limit,
    )
    await attach_public_coaches(db, page.items)
    return page


@router.get("/deleted", response_model=PaginatedResponse[EventResponse])
async def list_deleted_events(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List soft-deleted events (admin only)."""
    return await EventListingService(db).list_deleted_events(offset=offset, limit=limit)


@router.get("/occurrences/attendance", response_model=list[AttendanceRecordResponse])
async def list_attendance(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    from_time_utc: Annotated[int, Query(alias="fromTimeUtc")],
    to_time_utc: Annotated[int, Query(alias="toTimeUtc")],
):
    """List all attendance records in date range (admin/coach only)."""
    try:
        records = await AttendanceService(db).get_attendance_records(
            from_time_utc=from_time_utc, to_time_utc=to_time_utc
        )
    except RangeTooLargeException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RANGE_TOO_LARGE",
                "message": "Date range cannot exceed 1 year",
            },
        )
    return [AttendanceRecordResponse.from_model(r) for r in records]


@router.get("/by_id/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Get event by ID (admin/coach only)."""
    response = await event_response(db, await EventService(db).get_event(event_id))
    await attach_public_coaches(db, [response])
    return response


@router.get("/by_id/{event_id}/eligible", response_model=list[EligibleUserInfo])
async def list_eligible_users(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Users who can be assigned to or invited to this event (admin/coach only)."""
    users = await EventListingService(db).list_eligible_users(event_id)
    return [
        EligibleUserInfo(
            username=u.username,
            first_name=u.first_name,
            last_name=u.last_name,
            nickname=u.nickname,
        )
        for u in users
    ]


@router.get("/by_id/{event_id}/enrollments", response_model=EnrollmentListResponse)
async def list_enrollments(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    """List enrollments for an event (admin/coach only)."""
    return await EnrollmentService(db).list_enrollments(
        event_id, status_filter=status_filter
    )


# =============================================================================
# Writes
# =============================================================================


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    request: Request,
    data: EventCreate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Create a new event (admin/coach only)."""
    try:
        event = await EventService(db).create_event(
            **data.to_service_kwargs(), default_organizer=current_user.username
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.CREATE_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={"title": data.title, "type": data.type, "visibility": data.visibility},
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.patch("/by_id/{event_id}", response_model=EventResponse)
async def update_event(
    request: Request,
    event_id: int,
    data: EventUpdate,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Update a camp's or one-off's metadata (admin/organizer only)."""
    service = EventService(db)
    event = await service.get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    require_verb(event, EventVerb.update)
    try:
        event, changes = await service.update_event(
            event_id,
            **data.to_service_kwargs(),
            fields_set=data.model_fields_set,
            expected_version=data.version,
            actor=current_user.username,
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.UPDATE_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details=changes.to_audit_dict(),
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.patch("/by_id/{event_id}/correction", response_model=EventResponse)
async def correct_event(
    request: Request,
    event_id: int,
    data: EventCorrectionRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Correct a programme's identity, eligibility and presentation, for every
    occurrence past and future (admin/organizer only; programme R21–R22a)."""
    service = EventService(db)
    event = await service.get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    require_verb(event, EventVerb.correction)
    try:
        event, changes = await service.correct_event(
            event_id,
            title=data.title,
            description=data.description,
            visibility=data.visibility,
            gender=data.gender,
            min_age=data.min_age,
            max_age=data.max_age,
            strict_age=data.strict_age,
            is_featured=data.is_featured,
            gallery_uris=data.gallery_uris,
            sessions=data.sessions,
            schedule_id=data.schedule_id,
            short_description=data.short_description,
            stamp=data.stamp,
            highlights=data.highlights,
            includes=data.includes,
            fields_set=data.model_fields_set,
            expected_version=data.version,
            actor=current_user.username,
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.CORRECT_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details=changes.to_audit_dict(),
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.patch("/by_id/{event_id}/future", response_model=EventResponse)
async def update_event_future(
    request: Request,
    event_id: int,
    data: EventUpdateFutureRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Split a programme: close its current schedule at the cutoff and open the
    next with the new terms (admin/organizer only; programme R23–R25a)."""
    service = ProgrammeService(db)
    event = await service.get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    require_verb(event, EventVerb.future_split)
    try:
        event, changes = await service.split(
            event_id,
            cutoff_ms=data.effective_date_time_utc,
            venue_id=data.venue_id,
            organizer_name=data.organizer_name,
            coach_names=data.coach_names,
            start_time=data.start_time_utc,
            end_time=data.end_time_utc,
            rrule=data.rrule,
            sessions=data.sessions,
            update_sessions="sessions" in data.model_fields_set,
            expected_version=data.version,
            actor=current_user.username,
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.SPLIT_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={"cutoff": data.effective_date_time_utc, **changes.to_audit_dict()},
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/cancel", response_model=EventResponse)
async def cancel_event(
    request: Request,
    event_id: int,
    data: EventCancelRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Cancel a camp series from an occurrence start (admin/organizer only; camp R5).

    A programme is terminated (``/terminate``) and a one-off dropped
    (``/drop``); neither takes an effective time here (one-off R4).
    """
    service = CampService(db)
    event = await service.get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    if EventVerb.drop in _verbs_of(event):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_STATE",
                "message": "A one-off is dropped, not cancelled from a date; use /drop",
            },
        )
    require_verb(event, EventVerb.cancel)
    try:
        event, override_used = await service.cancel_series(
            event_id,
            reason=data.reason,
            effective_time=data.effective_date_time_utc,
            is_super_admin=bool(current_user.is_super_admin),
            expected_version=data.version,
            actor=current_user.username,
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.CANCEL_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={
            "reason": data.reason,
            "until_time": event.cutoff,
            "super_admin_override": override_used,
        },
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


def _verbs_of(event) -> set[EventVerb]:
    from ..services.event_types import offers

    return {verb for verb in EventVerb if offers(event, verb)}


@router.post("/by_id/{event_id}/undo-cancel", response_model=EventResponse)
async def undo_cancel_event(
    request: Request,
    event_id: int,
    data: EventVersionRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Reverse a camp series cancellation (admin/organizer only; camp R5d)."""
    service = CampService(db)
    event = await service.get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    require_verb(event, EventVerb.cancel)
    try:
        event = await service.undo_cancel_series(
            event_id, expected_version=data.version, actor=current_user.username
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.UNDO_CANCEL_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/reschedule", response_model=EventResponse)
async def reschedule_event(
    request: Request,
    event_id: int,
    data: EventRescheduleRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Reschedule a camp or one-off in place (admin/organizer only).

    A programme is split instead (programme R26). Allowed only before the
    series starts and while no override exists unless ``resetOverrides``.
    """
    base = EventService(db)
    event = await base.get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, current_user)
    require_reschedulable(event)
    service = (
        OneOffService(db) if EventVerb.drop in _verbs_of(event) else CampService(db)
    )
    sessions_provided = "sessions" in data.model_fields_set
    try:
        updated = await service.reschedule(
            event_id,
            start_time=data.start_time_utc,
            end_time=data.end_time_utc,
            rrule=data.rrule,
            venue_id=data.venue_id,
            sessions=data.sessions,
            update_sessions=sessions_provided,
            reset_overrides=data.reset_overrides,
            expected_version=data.version,
            actor=current_user.username,
        )
    except _SCHEDULE_EXCEPTIONS as e:
        _raise_http(e)
    details: dict[str, object | None] = {
        "start_time_utc": data.start_time_utc,
        "end_time_utc": data.end_time_utc,
        "rrule": data.rrule,
        "venue_id": data.venue_id,
        "reset_overrides": data.reset_overrides,
    }
    if sessions_provided:
        details["sessions"] = (
            [s.model_dump() for s in data.sessions]
            if data.sessions is not None
            else None
        )
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.RESCHEDULE_EVENT,
        resource_type="event",
        resource_id=str(event_id),
        details=details,
        ip_address=get_client_ip(request),
    )
    return await event_response(db, updated)


@router.delete("/by_id/{event_id}", response_model=EventResponse)
async def delete_event(
    request: Request,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Soft delete an event (admin only)."""
    event = await EventService(db).soft_delete_event(
        event_id, actor=current_user.username
    )
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.SOFT_DELETE_EVENT,
        resource_type="event",
        resource_id=str(event_id),
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/restore", response_model=EventResponse)
async def restore_event(
    request: Request,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
):
    """Restore a soft-deleted event (admin only)."""
    try:
        event = await EventService(db).restore_event(
            event_id, actor=current_user.username
        )
    except VenueIsDeletedException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VENUE_IS_DELETED",
                "message": "Cannot restore event: venue is deleted",
            },
        )
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.RESTORE_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.delete("/by_id/{event_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_event(
    request: Request,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_super_admin())],
):
    """Wipeout an event (hard delete with cascade). SuperAdmin only."""
    deletion_info = await EventService(db).hard_delete_event(event_id)
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.WIPEOUT_EVENT,
        resource_type="event",
        resource_id=str(event_id),
        details=deletion_info,
        ip_address=get_client_ip(request),
    )
