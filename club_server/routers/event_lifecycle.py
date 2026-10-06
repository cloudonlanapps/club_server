"""Lifecycle verbs per event type (#384, lifecycle L9): terminate, extend and
extend-indefinitely for programmes; drop and reinstate for one-offs; and the
schedule listing every type has.

Which type offers which verb is decided by ``services/event_types.py``; each
handler here parses the request, gates it, calls the type's service and
writes the audit row.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_current_active_user,
    get_db,
    require_admin_or_coach,
    require_organizer_or_admin,
)
from ..exceptions import (
    CancellationLeadTimeViolatedException,
    CancelledOccurrenceException,
    CutoffTooSoonException,
    EffectiveTimeNotSessionBoundaryException,
    InvalidStateException,
    PastOccurrenceException,
    StaleOccurrenceVersionException,
)
from ..schemas.event import (
    EventDropRequest,
    EventReinstateRequest,
    EventExtendIndefinitelyRequest,
    EventExtendRequest,
    EventResponse,
    EventScheduleResponse,
    EventTerminateRequest,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.event import EventService
from ..services.event_eligibility import event_response
from ..services.event_listing import EventListingService
from ..services.event_types import EventVerb, require_verb
from ..services.oneoff import OneOffService
from ..services.programme import ProgrammeService
from .occurrences import stale_occurrence_error
from ..utils import get_client_ip

router = APIRouter(prefix="/events", tags=["Events"])


def _invalid_state(e: InvalidStateException) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "INVALID_STATE", "message": e.message},
    )


def _cutoff_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, EffectiveTimeNotSessionBoundaryException):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "EFFECTIVE_TIME_NOT_SESSION_BOUNDARY",
                "message": "The cutoff must match an occurrence start of the current schedule",
            },
        )
    if isinstance(exc, CutoffTooSoonException):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "CUTOFF_TOO_SOON", "message": str(exc)},
        )
    if isinstance(exc, InvalidStateException):
        return _invalid_state(exc)
    raise exc


async def _gated_event(db: AsyncSession, event_id: int, user: User, verb: EventVerb):
    event = await EventService(db).get_live_event(event_id)
    require_organizer_or_admin(event.organizer_name, user)
    require_verb(event, verb)
    return event


@router.post("/by_id/{event_id}/terminate", response_model=EventResponse)
async def terminate_event(
    request: Request,
    event_id: int,
    data: EventTerminateRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """End a running programme at a cutoff (admin/organizer only; programme R1–R5)."""
    _ = await _gated_event(db, event_id, current_user, EventVerb.terminate)
    try:
        event, before = await ProgrammeService(db).terminate(
            event_id,
            reason=data.reason,
            cutoff_ms=data.cutoff_time_utc,
            actor=current_user.username,
        )
    except (
        EffectiveTimeNotSessionBoundaryException,
        CutoffTooSoonException,
        InvalidStateException,
    ) as e:
        raise _cutoff_errors(e) from e
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.TERMINATE_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={
            "reason": data.reason,
            "cutoff_before": before,
            "cutoff_after": data.cutoff_time_utc,
        },
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/extend", response_model=EventResponse)
async def extend_event(
    request: Request,
    event_id: int,
    data: EventExtendRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Move a terminated programme's cutoff, later or earlier (R6, R7)."""
    _ = await _gated_event(db, event_id, current_user, EventVerb.extend)
    try:
        event, before = await ProgrammeService(db).extend(
            event_id,
            cutoff_ms=data.cutoff_time_utc,
            reason=data.reason,
            actor=current_user.username,
        )
    except (
        EffectiveTimeNotSessionBoundaryException,
        CutoffTooSoonException,
        InvalidStateException,
    ) as e:
        raise _cutoff_errors(e) from e
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.EXTEND_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={
            "reason": data.reason,
            "cutoff_before": before,
            "cutoff_after": data.cutoff_time_utc,
        },
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/extend-indefinitely", response_model=EventResponse)
async def extend_event_indefinitely(
    request: Request,
    event_id: int,
    data: EventExtendIndefinitelyRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Remove a terminated programme's cutoff (R8)."""
    _ = await _gated_event(db, event_id, current_user, EventVerb.extend)
    try:
        event, before = await ProgrammeService(db).extend(
            event_id, cutoff_ms=None, reason=data.reason, actor=current_user.username
        )
    except InvalidStateException as e:
        raise _invalid_state(e) from e
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.EXTEND_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={"reason": data.reason, "cutoff_before": before, "cutoff_after": None},
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/drop", response_model=EventResponse)
async def drop_event(
    request: Request,
    event_id: int,
    data: EventDropRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Call off a one-off (admin/organizer only; one-off R3–R5a, R8)."""
    _ = await _gated_event(db, event_id, current_user, EventVerb.drop)
    try:
        event, override_used = await OneOffService(db).drop(
            event_id,
            reason=data.reason,
            is_super_admin=bool(current_user.is_super_admin),
            actor=current_user.username,
            expected_version=data.version,
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
                "message": "Cannot drop a past occasion",
            },
        )
    except CancelledOccurrenceException:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CANCELLED_OCCURRENCE",
                "message": "The one-off is already dropped",
            },
        )
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.DROP_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        details={"reason": data.reason, "super_admin_override": override_used},
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.post("/by_id/{event_id}/reinstate", response_model=EventResponse)
async def reinstate_event(
    request: Request,
    event_id: int,
    data: EventReinstateRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Put a dropped one-off back on (admin/organizer only; one-off R6, R7)."""
    _ = await _gated_event(db, event_id, current_user, EventVerb.reinstate)
    try:
        event = await OneOffService(db).reinstate(
            event_id, expected_version=data.version, actor=current_user.username
        )
    except StaleOccurrenceVersionException as e:
        raise stale_occurrence_error(e) from e
    except InvalidStateException as e:
        raise _invalid_state(e) from e
    await AuditService(db).log(
        actor_username=current_user.username,
        action=AuditAction.REINSTATE_EVENT,
        resource_type="event",
        resource_id=str(event.id),
        ip_address=get_client_ip(request),
    )
    return await event_response(db, event)


@router.get("/by_id/{event_id}/schedules", response_model=list[EventScheduleResponse])
async def list_event_schedules(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """The event's schedules in timeline order (admin/coach only)."""
    return await EventListingService(db).list_schedules(event_id)
