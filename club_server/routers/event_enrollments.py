"""Enrollment administration on an event (admin/organizer): invite, assign,
assign-trial, approve, reject, remove, and the withdrawal decisions.

Split from ``routers/events.py`` so each router stays within the file-size
limit; the paths are unchanged.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_admin_or_coach, require_organizer_or_admin
from ..exceptions import (
    AlreadyEnrolledException,
    EnrollmentTimeConflictException,
    EnrollmentTransitionException,
    InvalidStateException,
    UserNotEligibleForEventException,
)
from ..schemas.enrollment import (
    ApproveRequest,
    ApproveWithdrawRequest,
    AssignRequest,
    AssignTrialRequest,
    InviteRequest,
    RejectRequest,
    RejectWithdrawRequest,
    RemoveRequest,
)
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.enrollment import EnrollmentService
from ..services.event_types import EventVerb, require_verb
from ..utils import get_client_ip

router = APIRouter(prefix="/events", tags=["Events"])


async def _apply_to_each_member(
    membernames: list[str],
    *,
    audit_service: "AuditService",
    actor_username: str,
    audit_action: AuditAction,
    event_id: int,
    action,
    audit_details: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Run ``action(membername)`` once per unique entry, audit-logging each call.

    Duplicate names are coalesced silently while preserving order. Used by the
    enrollment-admin handlers (invite / assign / approve / reject / remove /
    approve-withdraw / reject-withdraw) which all share the same shape.
    """
    for membername in dict.fromkeys(membernames):
        await action(membername)
        await audit_service.log(
            actor_username=actor_username,
            action=audit_action,
            target_username=membername,
            resource_type="event",
            resource_id=str(event_id),
            details=audit_details,
            ip_address=ip_address,
        )


# =============================================================================
# Enrollment Admin Endpoints (admin/organizer)
# =============================================================================


@router.post(
    "/by_id/{event_id}/enrollments/invite", status_code=status.HTTP_204_NO_CONTENT
)
async def invite_users(
    request: Request,
    event_id: int,
    data: InviteRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Invite users to an event (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _invite(membername: str) -> None:
            await enrollment_service.invite_user(
                event_id,
                membername,
                is_super_admin=is_super,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.ENROLLMENT_INVITED,
            event_id=event_id,
            action=_invite,
            ip_address=get_client_ip(request),
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except AlreadyEnrolledException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENROLLED",
                "message": f"User '{e.membername}' already has active enrollment",
            },
        )
    except UserNotEligibleForEventException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "USER_NOT_ELIGIBLE_FOR_EVENT", "message": str(e)},
        )


@router.post(
    "/by_id/{event_id}/enrollments/assign", status_code=status.HTTP_204_NO_CONTENT
)
async def assign_users(
    request: Request,
    event_id: int,
    data: AssignRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Assign users to an event directly (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _assign(membername: str) -> None:
            await enrollment_service.assign_user(
                event_id,
                membername,
                is_super_admin=is_super,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.ENROLLMENT_ASSIGNED,
            event_id=event_id,
            action=_assign,
            ip_address=get_client_ip(request),
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except AlreadyEnrolledException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENROLLED",
                "message": f"User '{e.membername}' already has active enrollment",
            },
        )
    except EnrollmentTimeConflictException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TIME_CONFLICT",
                "message": f"User '{e.membername}' has time conflicts with existing enrollments",
                "conflicting_event_ids": e.conflicting_event_ids,
            },
        )
    except UserNotEligibleForEventException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "USER_NOT_ELIGIBLE_FOR_EVENT", "message": str(e)},
        )


@router.post(
    "/by_id/{event_id}/enrollments/assign-trial", status_code=status.HTTP_204_NO_CONTENT
)
async def assign_trial(
    request: Request,
    event_id: int,
    data: AssignTrialRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Assign a trial to a user (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)
        require_verb(event, EventVerb.assign_trial)

        await enrollment_service.assign_trial(
            event_id,
            data.membername,
            is_super_admin=bool(current_user.is_super_admin),
        )
        await audit_service.log(
            actor_username=current_user.username,
            action=AuditAction.ENROLLMENT_ASSIGNED,
            target_username=data.membername,
            resource_type="event",
            resource_id=str(event_id),
            details={"is_trial": True},
            ip_address=get_client_ip(request),
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
    except AlreadyEnrolledException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENROLLED",
                "message": f"User '{e.membername}' already has active enrollment",
            },
        )
    except EnrollmentTimeConflictException as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TIME_CONFLICT",
                "message": f"User '{e.membername}' has time conflicts with existing enrollments",
                "conflicting_event_ids": e.conflicting_event_ids,
            },
        )
    except UserNotEligibleForEventException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "USER_NOT_ELIGIBLE_FOR_EVENT", "message": str(e)},
        )


@router.post(
    "/by_id/{event_id}/enrollments/approve", status_code=status.HTTP_204_NO_CONTENT
)
async def approve_requests(
    request: Request,
    event_id: int,
    data: ApproveRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Approve enrollment requests (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _approve(membername: str) -> None:
            await enrollment_service.approve_request(
                event_id,
                membername,
                is_super_admin=is_super,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.ENROLLMENT_ACCEPTED,
            event_id=event_id,
            action=_approve,
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot approve enrollment with status '{e.current_status}'",
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
                "message": f"User '{e.membername}' has time conflicts with existing enrollments",
                "conflicting_event_ids": e.conflicting_event_ids,
            },
        )
    except UserNotEligibleForEventException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "USER_NOT_ELIGIBLE_FOR_EVENT", "message": str(e)},
        )


@router.post(
    "/by_id/{event_id}/enrollments/reject", status_code=status.HTTP_204_NO_CONTENT
)
async def reject_requests(
    request: Request,
    event_id: int,
    data: RejectRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Reject enrollment requests (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _reject(membername: str) -> None:
            await enrollment_service.reject_request(
                event_id,
                membername,
                reason=data.reason,
                is_super_admin=is_super,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.ENROLLMENT_REJECTED,
            event_id=event_id,
            action=_reject,
            audit_details={"reason": data.reason} if data.reason else None,
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot reject enrollment with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


@router.post(
    "/by_id/{event_id}/enrollments/remove", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_enrollments(
    request: Request,
    event_id: int,
    data: RemoveRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Remove enrollments from event (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _remove(membername: str) -> None:
            await enrollment_service.remove_enrollment(
                event_id,
                membername,
                reason=data.reason,
                is_super_admin=is_super,
                credit_disposition=data.credit_disposition,
                actor=current_user.username,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.ENROLLMENT_REMOVED,
            event_id=event_id,
            action=_remove,
            audit_details={"reason": data.reason} if data.reason else None,
            ip_address=get_client_ip(request),
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


@router.post(
    "/by_id/{event_id}/enrollments/approve-withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def approve_withdrawals(
    request: Request,
    event_id: int,
    data: ApproveWithdrawRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Approve withdrawal requests (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _approve_withdraw(membername: str) -> None:
            await enrollment_service.approve_withdrawal(
                event_id,
                membername,
                is_super_admin=is_super,
                credit_disposition=data.credit_disposition,
                actor=current_user.username,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.WITHDRAWAL_APPROVED,
            event_id=event_id,
            action=_approve_withdraw,
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot approve withdrawal with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )


@router.post(
    "/by_id/{event_id}/enrollments/reject-withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reject_withdrawals(
    request: Request,
    event_id: int,
    data: RejectWithdrawRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin_or_coach())],
):
    """Reject withdrawal requests (admin/organizer only)."""
    enrollment_service = EnrollmentService(db)
    audit_service = AuditService(db)

    try:
        event = await enrollment_service.get_event_or_raise(event_id)
        require_organizer_or_admin(event.organizer_name, current_user)

        is_super = bool(current_user.is_super_admin)

        async def _reject_withdraw(membername: str) -> None:
            await enrollment_service.reject_withdrawal(
                event_id,
                membername,
                reason=data.reason,
                is_super_admin=is_super,
            )

        await _apply_to_each_member(
            data.membernames,
            audit_service=audit_service,
            actor_username=current_user.username,
            audit_action=AuditAction.WITHDRAWAL_REJECTED,
            event_id=event_id,
            action=_reject_withdraw,
            audit_details={"reason": data.reason} if data.reason else None,
            ip_address=get_client_ip(request),
        )
    except EnrollmentTransitionException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_TRANSITION",
                "message": f"Cannot reject withdrawal with status '{e.current_status}'",
            },
        )
    except InvalidStateException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_STATE", "message": e.message},
        )
