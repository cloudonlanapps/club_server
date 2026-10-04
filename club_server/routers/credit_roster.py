"""Who on this programme can be marked (#294).

The one endpoint where an event id belongs in the path, because it is the
only collection an event owns. Answers R90: which members currently hold
no usable credit, and whose credit is about to lapse.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.enrollment import Enrollment, EnrollmentStatus
from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin_or_coach,
    require_credit_system_enabled,
)
from ..exceptions import CreditNotApplicableException
from ..schemas.common import PaginatedResponse
from ..schemas.credit import MemberCreditStatusResponse
from ..services.credit import CreditService
from ..services.credit_selection import CreditSelection

router = APIRouter(
    prefix="/events",
    tags=["Credits"],
    dependencies=[Depends(require_credit_system_enabled)],
)

# Members on the programme who can be charged. A pending withdrawal leaves
# the member enrolled and chargeable (R74). Invitations and pending requests
# are not on the roster yet.
ENROLLED_STATUSES = {
    EnrollmentStatus.accepted.value,
    EnrollmentStatus.assigned.value,
    EnrollmentStatus.assigned_trial.value,
    EnrollmentStatus.withdraw_requested.value,
}


@router.get(
    "/by_id/{event_id}/credits",
    response_model=PaginatedResponse[MemberCreditStatusResponse],
)
async def list_roster_credit(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
    state: str | None = Query(
        None, description='Restrict to "blocked" or "expiringSoon".'
    ),
    expiring_before_utc: int | None = Query(None, alias="expiringBeforeUtc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[MemberCreditStatusResponse]:
    """Credit standing of every enrolled member on a programme.

    ``blocked`` is derived, not stored: a member is blocked precisely when
    they hold no usable credit, so opening an account clears it with no
    admin step (R42, R43). ``boundCredits`` is the figure a departure has to
    resolve (R71, R72a): expired bound accounts count, general ones do not.
    """
    credits = CreditService(db)
    try:
        root_id = await credits.programme_id(event_id)
    except CreditNotApplicableException as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "CREDIT_NOT_APPLICABLE", "message": str(e)},
        )

    result = await db.execute(
        select(Enrollment)
        .where(
            Enrollment.event_id == event_id,
            Enrollment.status.in_(ENROLLED_STATUSES),
        )
        .order_by(Enrollment.membername)
    )
    enrollments = list(result.scalars().all())

    selection = CreditSelection(db)
    rows: list[MemberCreditStatusResponse] = []
    for enrollment in enrollments:
        is_trial = bool(enrollment.is_trial)
        accounts = await selection.usable_accounts(
            enrollment.membername, root_id, is_trial=is_trial
        )
        usable = sum(view.balance for view in accounts)
        rows.append(
            MemberCreditStatusResponse(
                membername=enrollment.membername,
                usable_credits=usable,
                bound_credits=await credits.bound_balance(
                    enrollment.membername, root_id
                ),
                paying_account_id=accounts[0].account.code if accounts else None,
                blocked=usable < 1,
                next_expiry_utc=(
                    min(view.account.valid_until for view in accounts)
                    if accounts
                    else None
                ),
            )
        )

    if state == "blocked":
        rows = [row for row in rows if row.blocked]
    elif state == "expiringSoon":
        rows = [
            row
            for row in rows
            if row.next_expiry_utc is not None
            and expiring_before_utc is not None
            and row.next_expiry_utc <= expiring_before_utc
        ]

    return PaginatedResponse[MemberCreditStatusResponse](
        items=rows[offset : offset + limit],
        total=len(rows),
        offset=offset,
        limit=limit,
    )
