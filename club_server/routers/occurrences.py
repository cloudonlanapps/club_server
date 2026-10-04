from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..utils import get_client_ip
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_event_coach_or_organizer_or_admin,
    require_organizer_or_admin,
)
from ..schemas.occurrence import (
    OccurrenceCancelRequest,
    OccurrenceRescheduleRequest,
    OccurrenceVersionRequest,
    OccurrenceResponse,
)
from ..schemas.attendance import (
    ApproveLeaveRequest,
    AttendanceRecordResponse,
    BulkMarkAttendanceRequest,
    BulkMarkAttendanceResponse,
    MarkedAttendanceEntry,
    RefusedAttendanceEntry,
    TrialEndedAttendanceEntry,
    RejectLeaveRequest,
)
from ..exceptions import (
    AttendanceNotYetOpenException,
    InsufficientCreditException,
    CancellationLeadTimeViolatedException,
    CancelledOccurrenceException,
    EditWindowClosedException,
    InvalidAttendanceStatusException,
    InvalidSessionsException,
    InvalidStateException,
    NothingToRescheduleException,
    PastOccurrenceException,
    PostponeOnlyException,
    RangeTooLargeException,
    RescheduleLeadTimeViolatedException,
    StaleOccurrenceVersionException,
    VenueNotFoundException,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.attendance import AttendanceService
from ..services.occurrence import OccurrenceService

router = APIRouter(tags=["Occurrences"])


def stale_occurrence_error(exc: StaleOccurrenceVersionException) -> HTTPException:
    """409 for a change carrying a version the occurrence has moved past (L23a)."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "STALE_VERSION",
            "message": "The occurrence was changed since you last loaded it",
            "version": exc.version,
            "updatedAt": exc.updated_at,
            "updatedBy": exc.updated_by,
        },
    )


# =============================================================================
# Query Endpoints (admin/coach)
# =============================================================================


@router.get("/events/occurrences", response_model=list[OccurrenceResponse])
async def list_occurrences(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
    from_time_utc: Annotated[int, Query(alias="fromTimeUtc")],
    to_time_utc: Annotated[int, Query(alias="toTimeUtc")],
    event_type: Annotated[str | None, Query(alias="type")] = None,
    visibility: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
):
    """List occurrences in date range (admin/coach only)."""
    occurrence_service = OccurrenceService(db)

    try:
        return await occurrence_service.list_occurrences(
            from_time_utc=from_time_utc,
            to_time_utc=to_time_utc,
            event_type=event_type,
            visibility=visibility,
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
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}",
    response_model=OccurrenceResponse,
)
async def get_occurrence(
    event_id: int,
    occurrence_time_utc: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Get specific occurrence (admin/coach only)."""
    occurrence_service = OccurrenceService(db)

    return await occurrence_service.get_occurrence(event_id, occurrence_time_utc)


@router.get(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
    response_model=list[AttendanceRecordResponse],
)
async def get_occurrence_attendance(
    event_id: int,
    occurrence_time_utc: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Get attendance records for an occurrence (admin/coach only)."""
    attendance_service = AttendanceService(db)
    records = await attendance_service.get_occurrence_attendance(
        event_id, occurrence_time_utc
    )
    return [AttendanceRecordResponse.from_model(r) for r in records]


# =============================================================================
# Occurrence Mutation Endpoints (admin/organizer)
# =============================================================================


@router.post(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/reschedule",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reschedule_occurrence(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    data: OccurrenceRescheduleRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Reschedule an occurrence (admin/organizer only)."""
    occurrence_service = OccurrenceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        override_used = await occurrence_service.reschedule_occurrence(
            event_id,
            occurrence_time_utc,
            new_start_time=data.new_start_time_utc,
            new_duration_minutes=data.new_duration_minutes,
            new_venue_id=data.new_venue_id,
            is_super_admin=bool(current_user.is_super_admin),
            expected_version=data.version,
            actor=current_user.username,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.RESCHEDULE_OCCURRENCE,
            resource_type="occurrence",
            resource_id=f"{event.id}:{occurrence_time_utc}",
            details={
                "event_id": event.id,
                "occurrence_time_utc": occurrence_time_utc,
                "new_start_time_utc": data.new_start_time_utc,
                "new_duration_minutes": data.new_duration_minutes,
                "new_venue_id": data.new_venue_id,
                "super_admin_override": override_used,
            },
            ip_address=get_client_ip(request),
        )
    except StaleOccurrenceVersionException as e:
        raise stale_occurrence_error(e) from e
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except NothingToRescheduleException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "NOTHING_TO_RESCHEDULE",
                "message": "At least one of start, duration, or venue must be set",
            },
        )
    except PostponeOnlyException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "POSTPONE_ONLY", "message": str(e)},
        )
    except InvalidSessionsException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": e.code, "message": str(e)},
        )
    except RescheduleLeadTimeViolatedException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RESCHEDULE_LEAD_TIME_VIOLATED",
                "message": "Reschedule must be at least 30 minutes before the occurrence",
            },
        )
    except VenueNotFoundException:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "VENUE_NOT_FOUND", "message": "Venue not found"},
        )
    except PastOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "PAST_OCCURRENCE",
                "message": "Cannot reschedule past occurrence",
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "Cannot reschedule cancelled occurrence",
            },
        )


@router.post(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_occurrence(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    data: OccurrenceCancelRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Cancel an occurrence (admin/organizer only)."""
    occurrence_service = OccurrenceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        override_used = await occurrence_service.cancel_occurrence(
            event_id,
            occurrence_time_utc,
            is_super_admin=bool(current_user.is_super_admin),
            actor=current_user.username,
            reason=data.reason,
            expected_version=data.version,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.CANCEL_OCCURRENCE,
            resource_type="occurrence",
            resource_id=f"{event.id}:{occurrence_time_utc}",
            details={
                "event_id": event.id,
                "occurrence_time_utc": occurrence_time_utc,
                "reason": data.reason,
                "super_admin_override": override_used,
            },
            ip_address=get_client_ip(request),
        )
    except StaleOccurrenceVersionException as e:
        raise stale_occurrence_error(e) from e
    except CancellationLeadTimeViolatedException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANCELLATION_LEAD_TIME_VIOLATED",
                "message": "Cancellation must be at least 30 minutes before the occurrence",
            },
        )
    except PastOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "PAST_OCCURRENCE",
                "message": "Cannot cancel past occurrence",
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "Occurrence is already cancelled",
            },
        )


@router.post(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/undo-cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def undo_cancel_occurrence(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    data: OccurrenceVersionRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Undo cancellation of an occurrence (admin/organizer only)."""
    occurrence_service = OccurrenceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        override_used = await occurrence_service.restore_occurrence(
            event_id,
            occurrence_time_utc,
            expected_version=data.version,
            is_super_admin=bool(current_user.is_super_admin),
            actor=current_user.username,
        )

        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.UNDO_CANCEL_OCCURRENCE,
            resource_type="occurrence",
            resource_id=f"{event.id}:{occurrence_time_utc}",
            details={
                "event_id": event.id,
                "occurrence_time_utc": occurrence_time_utc,
                "super_admin_override": override_used,
            },
            ip_address=get_client_ip(request),
        )
    except StaleOccurrenceVersionException as e:
        raise stale_occurrence_error(e) from e
    except CancellationLeadTimeViolatedException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANCELLATION_LEAD_TIME_VIOLATED",
                "message": "A cancellation can be undone only until 30 minutes before the occurrence",
            },
        )
    except PastOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "PAST_OCCURRENCE",
                "message": "Cannot undo the cancellation of a past occurrence",
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "The occurrence falls at or after the series cutoff",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "NOT_CANCELLED",
                "message": e.message,
            },
        )


# =============================================================================
# Attendance Admin Endpoints (admin/coach)
# =============================================================================


@router.post(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
    response_model=BulkMarkAttendanceResponse,
)
async def mark_attendance(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    data: BulkMarkAttendanceRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
) -> BulkMarkAttendanceResponse:
    """Mark attendance for one or more users (admin, organizer or assigned coach; #247).

    Members are settled independently (#294, R41b): a member who cannot pay
    for the session is refused and reported, and everyone else is still
    marked. Insufficient credit is an expected business state, not a
    malformed request, and must not be able to fail a whole register.

    A member whose mark spent the last of their trial credit is removed from
    the programme (R52) and listed in ``trialEnded`` as well as ``marked``.

    Where the deployment does not run on credits, ``refused`` and
    ``trialEnded`` are always empty — the shape does not vary with
    configuration (R97).
    """
    occurrence_service = OccurrenceService(db)
    attendance_service = AttendanceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_event_coach_or_organizer_or_admin(event, current_user)

        is_super_admin = bool(current_user.is_super_admin)
        await attendance_service.check_open_window(
            event_id, occurrence_time_utc, is_super_admin
        )
        await attendance_service.check_edit_window(
            event_id, occurrence_time_utc, is_super_admin
        )

        marked: list[MarkedAttendanceEntry] = []
        refused: list[RefusedAttendanceEntry] = []
        trial_ended: list[TrialEndedAttendanceEntry] = []
        for record_req in data.records:
            try:
                ended = await attendance_service.mark_attendance(
                    event_id,
                    occurrence_time_utc,
                    record_req.membername,
                    record_req.status,
                    notes=record_req.notes,
                    is_super_admin=is_super_admin,
                    actor=current_user.username,
                )
            except InsufficientCreditException as e:
                # Nothing was written for this member (R41a), so the session
                # is clean and the remaining members can still be settled.
                refused.append(
                    RefusedAttendanceEntry(
                        membername=record_req.membername,
                        code="INSUFFICIENT_CREDIT",
                        message=str(e),
                    )
                )
                continue
            marked.append(
                MarkedAttendanceEntry(
                    membername=record_req.membername, status=record_req.status
                )
            )
            if ended:
                trial_ended.append(
                    TrialEndedAttendanceEntry(membername=record_req.membername)
                )
            await audit_service.log(
                actor_username=current_user.username,
                action=AuditAction.ATTENDANCE_MARKED,
                target_username=record_req.membername,
                resource_type="occurrence",
                resource_id=f"{event_id}:{occurrence_time_utc}",
                details={"status": record_req.status},
                ip_address=get_client_ip(request),
            )
        return BulkMarkAttendanceResponse(
            marked=marked, refused=refused, trial_ended=trial_ended
        )
    except AttendanceNotYetOpenException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "ATTENDANCE_NOT_YET_OPEN",
                "message": "Attendance marking opens 30 minutes before the occurrence start",
            },
        )
    except EditWindowClosedException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "EDIT_WINDOW_CLOSED",
                "message": "Attendance edit window (15 days) has expired",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": str(e)},
        )
    except InvalidAttendanceStatusException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_ATTENDANCE_STATUS",
                "message": str(e),
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "Cannot mark attendance on a cancelled occurrence",
            },
        )


@router.delete(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance/{membername}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_attendance(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    membername: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Clear a member's attendance mark for an occurrence (admin, organizer or assigned coach).

    Deletes a present/absent/late record, returning the member to the
    unrecorded state. Same window gates as marking: super-admin bypasses the
    edit window but not the open window. Leave records are not cleared here.
    """
    occurrence_service = OccurrenceService(db)
    attendance_service = AttendanceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_event_coach_or_organizer_or_admin(event, current_user)

        is_super_admin = bool(current_user.is_super_admin)
        await attendance_service.check_open_window(
            event_id, occurrence_time_utc, is_super_admin
        )
        await attendance_service.check_edit_window(
            event_id, occurrence_time_utc, is_super_admin
        )

        await attendance_service.clear_attendance(
            event_id, occurrence_time_utc, membername, actor=current_user.username
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ATTENDANCE_CLEARED,
            target_username=membername,
            resource_type="occurrence",
            resource_id=f"{event_id}:{occurrence_time_utc}",
            ip_address=get_client_ip(request),
        )
    except AttendanceNotYetOpenException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "ATTENDANCE_NOT_YET_OPEN",
                "message": "Attendance marking opens 30 minutes before the occurrence start",
            },
        )
    except EditWindowClosedException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "EDIT_WINDOW_CLOSED",
                "message": "Attendance edit window (15 days) has expired",
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "Cannot clear attendance on a cancelled occurrence",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": str(e)},
        )


@router.post(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def approve_leave(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    data: ApproveLeaveRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Approve leave requests (admin, organizer or assigned coach; #247)."""
    occurrence_service = OccurrenceService(db)
    attendance_service = AttendanceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_event_coach_or_organizer_or_admin(event, current_user)
        await attendance_service.check_edit_window(
            event_id, occurrence_time_utc, bool(current_user.is_super_admin)
        )
    except EditWindowClosedException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "EDIT_WINDOW_CLOSED",
                "message": "Attendance edit window (15 days) has expired",
            },
        )

    for membername in data.membernames:
        try:
            await attendance_service.approve_leave(
                event_id,
                occurrence_time_utc,
                membername,
                is_super_admin=bool(current_user.is_super_admin),
                actor=current_user.username,
            )
            await audit_service.log(
                actor_username=current_user.username,
                action=AuditAction.LEAVE_APPROVED,
                target_username=membername,
                resource_type="occurrence",
                resource_id=f"{event_id}:{occurrence_time_utc}",
                ip_address=get_client_ip(request),
            )
        except CancelledOccurrenceException:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "CANCELLED_OCCURRENCE",
                    "message": "Cannot approve leave on a cancelled occurrence",
                },
            )
        except InvalidStateException as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_STATE", "message": str(e)},
            )


@router.post(
    "/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/reject",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reject_leave(
    request: Request,
    event_id: int,
    occurrence_time_utc: int,
    data: RejectLeaveRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Reject leave requests (admin, organizer or assigned coach; #247)."""
    occurrence_service = OccurrenceService(db)
    attendance_service = AttendanceService(db)
    audit_service = AuditService(db)

    try:
        event = await occurrence_service.get_event_or_raise(event_id)
        require_event_coach_or_organizer_or_admin(event, current_user)
        await attendance_service.check_edit_window(
            event_id, occurrence_time_utc, bool(current_user.is_super_admin)
        )
    except EditWindowClosedException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "EDIT_WINDOW_CLOSED",
                "message": "Attendance edit window (15 days) has expired",
            },
        )

    for membername in data.membernames:
        try:
            await attendance_service.reject_leave(
                event_id,
                occurrence_time_utc,
                membername,
                is_super_admin=bool(current_user.is_super_admin),
                actor=current_user.username,
            )
            await audit_service.log(
                actor_username=current_user.username,
                action=AuditAction.LEAVE_REJECTED,
                target_username=membername,
                resource_type="occurrence",
                resource_id=f"{event_id}:{occurrence_time_utc}",
                details={"reason": data.reason},
                ip_address=get_client_ip(request),
            )
        except CancelledOccurrenceException:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "CANCELLED_OCCURRENCE",
                    "message": "Cannot reject leave on a cancelled occurrence",
                },
            )
        except InvalidStateException as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_STATE", "message": str(e)},
            )
