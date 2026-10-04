from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..utils import get_client_ip
from ..dependencies import (
    get_current_active_user,
    get_db,
    require_self_or_event_staff,
    require_self_or_staff,
)
from ..schemas.attendance import AttendanceRecordResponse, DeclareLeaveRequest
from ..schemas.common import PaginatedResponse
from ..schemas.enrollment import EnrollmentResponse, WithdrawRequest
from ..schemas.event import EventResponse, EventScheduleResponse
from ..schemas.occurrence import OccurrenceResponse
from ..exceptions import (
    AlreadyEnrolledException,
    CancelledOccurrenceException,
    EnrollmentTimeConflictException,
    EnrollmentTransitionException,
    InvalidStateException,
    LeaveAlreadyDeclaredException,
    LeaveWindowClosedException,
    RangeTooLargeException,
    UserNotEligibleForEventException,
)
from ..services.attendance import AttendanceService
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.enrollment import EnrollmentService
from ..services.event import EventService
from ..services.event_listing import EventListingService
from ..services.occurrence import OccurrenceService

router = APIRouter(prefix="/myevents", tags=["My Events"])


# =============================================================================
# Query Endpoints
# =============================================================================


@router.get("/by_id/{username}", response_model=PaginatedResponse[EventResponse])
async def list_user_events(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    from_time_utc: Annotated[int | None, Query(alias="fromTimeUtc")] = None,
    to_time_utc: Annotated[int | None, Query(alias="toTimeUtc")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List events where the user is enrolled."""
    require_self_or_staff(username, current_user)
    event_service = EventListingService(db)
    return await event_service.list_user_events(
        membername=username,
        from_time_utc=from_time_utc,
        to_time_utc=to_time_utc,
        offset=offset,
        limit=limit,
    )


# Routes with fixed path segments must come before /{event_id} to avoid
# FastAPI trying to parse "occurrences" or "attendance" as an int event_id.


@router.get("/by_id/{username}/occurrences", response_model=list[OccurrenceResponse])
async def list_user_occurrences(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    from_time_utc: Annotated[int, Query(alias="fromTimeUtc")],
    to_time_utc: Annotated[int, Query(alias="toTimeUtc")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
):
    """List occurrences for events where the user is enrolled."""
    require_self_or_staff(username, current_user)
    occurrence_service = OccurrenceService(db)
    try:
        return await occurrence_service.list_user_occurrences(
            membername=username,
            from_time_utc=from_time_utc,
            to_time_utc=to_time_utc,
            offset=offset,
            limit=limit,
        )
    except RangeTooLargeException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RANGE_TOO_LARGE",
                "message": "Date range cannot exceed 1 year",
            },
        )


@router.get(
    "/by_id/{username}/attendance", response_model=list[AttendanceRecordResponse]
)
async def list_user_attendance(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    from_time_utc: Annotated[int, Query(alias="fromTimeUtc")],
    to_time_utc: Annotated[int, Query(alias="toTimeUtc")],
):
    """List attendance records for a user's enrolled events in a date range."""
    require_self_or_staff(username, current_user)

    attendance_service = AttendanceService(db)
    try:
        records = await attendance_service.get_user_attendance_records(
            membername=username,
            from_time_utc=from_time_utc,
            to_time_utc=to_time_utc,
        )
        return [AttendanceRecordResponse.from_model(r) for r in records]
    except RangeTooLargeException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RANGE_TOO_LARGE",
                "message": "Date range cannot exceed 1 year",
            },
        )


# Routes with {event_id} path parameter come after fixed-segment routes.


@router.get("/by_id/{username}/{event_id}", response_model=EventResponse)
async def get_user_event(
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get a single event the user has access to (enrolled or public)."""
    require_self_or_staff(username, current_user)
    event_service = EventService(db)
    event = await event_service.check_user_event_access(event_id, username)
    return EventResponse.from_model(event)


@router.get(
    "/by_id/{username}/{event_id}/schedules",
    response_model=list[EventScheduleResponse],
)
async def get_user_event_schedules(
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """The schedules of a user's event (enrolled or public), in timeline order."""
    require_self_or_staff(username, current_user)
    await EventService(db).check_user_event_access(event_id, username)
    return await EventListingService(db).list_schedules(event_id)


@router.get(
    "/by_id/{username}/{event_id}/enrollments", response_model=EnrollmentResponse
)
async def get_user_enrollment(
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get the user's enrollment details for an event."""
    require_self_or_staff(username, current_user)
    enrollment_service = EnrollmentService(db)
    enrollment = await enrollment_service.get_enrollment_or_raise(event_id, username)
    return EnrollmentResponse.from_model(enrollment)


@router.get(
    "/by_id/{username}/{event_id}/occurrences/{occurrence_time_utc}",
    response_model=OccurrenceResponse,
)
async def get_user_occurrence(
    username: str,
    event_id: int,
    occurrence_time_utc: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get a specific occurrence for a user's event (enrolled or public)."""
    require_self_or_staff(username, current_user)
    event_service = EventService(db)
    await event_service.check_user_event_access(event_id, username)
    occurrence_service = OccurrenceService(db)
    return await occurrence_service.get_occurrence(event_id, occurrence_time_utc)


@router.get(
    "/by_id/{username}/{event_id}/occurrences/{occurrence_time_utc}/attendance",
    response_model=AttendanceRecordResponse | None,
)
async def get_user_attendance(
    username: str,
    event_id: int,
    occurrence_time_utc: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get the user's attendance record for an occurrence."""
    require_self_or_staff(username, current_user)
    attendance_service = AttendanceService(db)
    record = await attendance_service.get_user_attendance(
        event_id, occurrence_time_utc, username
    )
    if record is None:
        return None
    return AttendanceRecordResponse.from_model(record)


# =============================================================================
# Enrollment Mutation Endpoints
# =============================================================================


@router.post(
    "/by_id/{username}/{event_id}/enrollments/accept",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def accept_invite(
    request: Request,
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Accept an invitation."""
    require_self_or_staff(username, current_user)
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await enrollment_service.accept_invite(
            event_id,
            username,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ENROLLMENT_ACCEPTED,
            target_username=username,
            resource_type="event",
            resource_id=str(event_id),
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot accept invitation with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except EnrollmentTimeConflictException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TIME_CONFLICT",
                "message": f"Time conflict with event(s): {e.conflicting_event_ids}",
            },
        )


@router.post(
    "/by_id/{username}/{event_id}/enrollments/decline",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def decline_invite(
    request: Request,
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Decline an invitation."""
    require_self_or_staff(username, current_user)
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await enrollment_service.decline_invite(
            event_id,
            username,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ENROLLMENT_DECLINED,
            target_username=username,
            resource_type="event",
            resource_id=str(event_id),
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot decline invitation with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


@router.post(
    "/by_id/{username}/{event_id}/enrollments/request",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def request_enrollment(
    request: Request,
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Request to join an event."""
    require_self_or_staff(username, current_user)
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await enrollment_service.request_enrollment(
            event_id,
            username,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ENROLLMENT_REQUESTED,
            target_username=username,
            resource_type="event",
            resource_id=str(event_id),
            ip_address=get_client_ip(request),
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except AlreadyEnrolledException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENROLLED",
                "message": "You already have an active enrollment for this event",
            },
        )
    except EnrollmentTimeConflictException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TIME_CONFLICT",
                "message": f"Time conflict with event(s): {e.conflicting_event_ids}",
            },
        )
    except UserNotEligibleForEventException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "USER_NOT_ELIGIBLE_FOR_EVENT", "message": str(e)},
        )


@router.post(
    "/by_id/{username}/{event_id}/enrollments/withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def request_withdrawal(
    request: Request,
    username: str,
    event_id: int,
    data: WithdrawRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Request to withdraw from an event."""
    require_self_or_staff(username, current_user)
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await enrollment_service.request_withdrawal(
            event_id,
            username,
            reason=data.reason,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.WITHDRAWAL_REQUESTED,
            target_username=username,
            resource_type="event",
            resource_id=str(event_id),
            details={"reason": data.reason} if data.reason else None,
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot request withdrawal with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


@router.post(
    "/by_id/{username}/{event_id}/enrollments/cancel-withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_withdrawal(
    request: Request,
    username: str,
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Cancel a withdrawal request."""
    require_self_or_staff(username, current_user)
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await enrollment_service.cancel_withdrawal(
            event_id,
            username,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.WITHDRAWAL_CANCELLED,
            target_username=username,
            resource_type="event",
            resource_id=str(event_id),
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot cancel withdrawal with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


# =============================================================================
# Leave Mutation Endpoints
# =============================================================================


@router.post(
    "/by_id/{username}/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def request_leave(
    request: Request,
    username: str,
    event_id: int,
    occurrence_time_utc: int,
    data: DeclareLeaveRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Declare leave for an occurrence."""
    require_self_or_staff(username, current_user)
    attendance_service = AttendanceService(db)
    audit_service = AuditService(db)

    try:
        event = await EnrollmentService(db).get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await attendance_service.declare_leave(
            event_id,
            occurrence_time_utc,
            username,
            data.reason,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.LEAVE_REQUESTED,
            target_username=username,
            resource_type="occurrence",
            resource_id=f"{event_id}:{occurrence_time_utc}",
            details={"reason": data.reason},
            ip_address=get_client_ip(request),
        )
    except LeaveWindowClosedException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "LEAVE_WINDOW_CLOSED",
                "message": "Leave must be declared at least 2 hours before occurrence",
            },
        )
    except LeaveAlreadyDeclaredException:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "LEAVE_ALREADY_DECLARED",
                "message": "Leave has already been declared for this occurrence",
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "Cannot declare leave on a cancelled occurrence",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": str(e)},
        )


@router.post(
    "/by_id/{username}/{event_id}/occurrences/{occurrence_time_utc}/leave/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_leave(
    request: Request,
    username: str,
    event_id: int,
    occurrence_time_utc: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Cancel a leave request."""
    require_self_or_staff(username, current_user)
    attendance_service = AttendanceService(db)
    audit_service = AuditService(db)

    try:
        event = await EnrollmentService(db).get_event_or_raise(event_id)
        require_self_or_event_staff(username, event, current_user)
        await attendance_service.cancel_leave(
            event_id,
            occurrence_time_utc,
            username,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.LEAVE_CANCELLED,
            target_username=username,
            resource_type="occurrence",
            resource_id=f"{event_id}:{occurrence_time_utc}",
            ip_address=get_client_ip(request),
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": str(e)},
        )
