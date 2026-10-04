"""Credit account lifecycle (#294).

Opening, extending, reversing and transferring. Reads live in
``credits_query.py``; the member's own view lives in ``mycredits.py``.

Every route here is gated by ``require_credit_system_enabled``: the
endpoints exist on every deployment so the published API does not vary
with configuration, but a deployment that does not run on credits refuses
them all with 503 before any handler body runs (R94, R94a).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_admin, require_credit_system_enabled
from ..exceptions import (
    CreditAccountClosedException,
    CreditAccountNotFoundException,
    CreditNotApplicableException,
    InsufficientBalanceException,
    InvalidCreditAmountException,
    InvalidValidityWindowException,
    SuperAdminCannotHoldCreditException,
    UserNotFoundException,
)
from ..schemas.credit import (
    CreditAccountResponse,
    ExtendValidityRequest,
    OpenAccountRequest,
    ReverseGrantRequest,
    TransferRequest,
    TransferResponse,
)
from ..services.audit_actions import AuditAction
from ..services.audit import AuditService
from ..services.credit import AccountView, CreditService
from ..services.credit_lifecycle import CreditLifecycleService
from ..utils import get_client_ip, now_utc_ms

router = APIRouter(
    prefix="/credits",
    tags=["Credits"],
    dependencies=[Depends(require_credit_system_enabled)],
)


def to_response(view: AccountView) -> CreditAccountResponse:
    """Shape one account, deriving ``balance``, ``state`` and ``usable``."""
    account = view.account
    now = now_utc_ms()
    return CreditAccountResponse(
        account_id=account.code,
        membername=account.membername,
        kind=account.kind,
        event_id=account.event_id,
        is_trial=account.is_trial,
        balance=view.balance,
        valid_from_utc=account.valid_from,
        valid_until_utc=account.valid_until,
        usable=account.is_usable(view.balance, now),
        state=account.state(view.balance, now),
        opened_at_utc=account.opened_at,
        opened_by=account.opened_by,
        closed_at_utc=account.closed_at,
    )


def _not_found(exc: CreditAccountNotFoundException) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "CREDIT_ACCOUNT_NOT_FOUND", "message": str(exc)},
    )


def _unprocessable(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )


@router.post(
    "/accounts",
    response_model=CreditAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def open_account(
    request: Request,
    data: OpenAccountRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> CreditAccountResponse:
    """Open a credit account for a member (admin only).

    A bound account names the programme it is bound to; an event keeps one
    id for life, so a later split cannot strand it.
    """
    service = CreditService(db)
    audit = AuditService(db)
    try:
        view = await service.open_account(
            membername=data.membername,
            credits=data.credits,
            valid_from=data.valid_from_utc,
            valid_until=data.valid_until_utc,
            reason=data.reason,
            actor=current_user.username,
            event_id=data.event_id,
            is_trial=data.is_trial,
        )
    except UserNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": str(e)},
        )
    except CreditNotApplicableException as e:
        raise _unprocessable("CREDIT_NOT_APPLICABLE", str(e))
    except SuperAdminCannotHoldCreditException as e:
        raise _unprocessable("SUPER_ADMIN_CANNOT_HOLD_CREDIT", str(e))
    except InvalidCreditAmountException as e:
        raise _unprocessable("INVALID_CREDIT_AMOUNT", str(e))
    except InvalidValidityWindowException as e:
        raise _unprocessable("INVALID_VALIDITY_WINDOW", str(e))

    await audit.log(
        actor_username=current_user.username,
        action=AuditAction.OPEN_CREDIT_ACCOUNT,
        target_username=data.membername,
        resource_type="credit_account",
        resource_id=view.account.code,
        details={"credits": data.credits, "eventId": view.account.event_id},
        ip_address=get_client_ip(request),
    )
    return to_response(view)


@router.post("/accounts/{account_id}/extend", response_model=CreditAccountResponse)
async def extend_validity(
    request: Request,
    account_id: str,
    data: ExtendValidityRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> CreditAccountResponse:
    """Move an account's validity end forwards (admin only)."""
    service = CreditLifecycleService(db)
    audit = AuditService(db)
    try:
        view = await service.extend_validity(
            code=account_id,
            valid_until=data.valid_until_utc,
            reason=data.reason,
            actor=current_user.username,
        )
    except CreditAccountNotFoundException as e:
        raise _not_found(e)
    except CreditAccountClosedException as e:
        raise _unprocessable("ACCOUNT_CLOSED", str(e))
    except InvalidValidityWindowException as e:
        raise _unprocessable("INVALID_VALIDITY_WINDOW", str(e))

    await audit.log(
        actor_username=current_user.username,
        action=AuditAction.EXTEND_CREDIT_ACCOUNT,
        target_username=view.account.membername,
        resource_type="credit_account",
        resource_id=account_id,
        details={"validUntilUtc": data.valid_until_utc, "reason": data.reason},
        ip_address=get_client_ip(request),
    )
    return to_response(view)


@router.post("/accounts/{account_id}/reverse", response_model=CreditAccountResponse)
async def reverse_grant(
    request: Request,
    account_id: str,
    data: ReverseGrantRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> CreditAccountResponse:
    """Undo a mistaken grant, in whole or in part (admin only)."""
    service = CreditLifecycleService(db)
    audit = AuditService(db)
    try:
        view = await service.reverse_grant(
            code=account_id,
            reason=data.reason,
            actor=current_user.username,
            credits=data.credits,
        )
    except CreditAccountNotFoundException as e:
        raise _not_found(e)
    except CreditAccountClosedException as e:
        raise _unprocessable("ACCOUNT_CLOSED", str(e))
    except InsufficientBalanceException as e:
        raise _unprocessable("INSUFFICIENT_BALANCE", str(e))
    except InvalidCreditAmountException as e:
        raise _unprocessable("INVALID_CREDIT_AMOUNT", str(e))

    await audit.log(
        actor_username=current_user.username,
        action=AuditAction.REVERSE_CREDIT_GRANT,
        target_username=view.account.membername,
        resource_type="credit_account",
        resource_id=account_id,
        details={"credits": data.credits, "reason": data.reason},
        ip_address=get_client_ip(request),
    )
    return to_response(view)


@router.post("/accounts/{account_id}/transfer", response_model=TransferResponse)
async def transfer_account(
    request: Request,
    account_id: str,
    data: TransferRequest,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> TransferResponse:
    """Close an account, moving what survives the penalty into a new one.

    Admin only. Serves expiry disposition, voluntary drop-out and admin
    removal alike — they differ only in the penalty. There is no
    destination to choose: the operation creates the account it moves into.
    """
    service = CreditLifecycleService(db)
    audit = AuditService(db)
    try:
        source, created = await service.transfer(
            code=account_id,
            penalty=data.penalty,
            valid_from=data.valid_from_utc,
            valid_until=data.valid_until_utc,
            reason=data.reason,
            actor=current_user.username,
        )
    except CreditAccountNotFoundException as e:
        raise _not_found(e)
    except CreditAccountClosedException as e:
        raise _unprocessable("ACCOUNT_CLOSED", str(e))
    except InvalidValidityWindowException as e:
        raise _unprocessable("INVALID_VALIDITY_WINDOW", str(e))

    await audit.log(
        actor_username=current_user.username,
        action=AuditAction.TRANSFER_CREDIT_ACCOUNT,
        target_username=source.account.membername,
        resource_type="credit_account",
        resource_id=account_id,
        details={
            "penalty": data.penalty,
            "createdAccountId": created.account.code if created else None,
            "reason": data.reason,
        },
        ip_address=get_client_ip(request),
    )
    return TransferResponse(
        source=to_response(source),
        created=to_response(created) if created else None,
    )
