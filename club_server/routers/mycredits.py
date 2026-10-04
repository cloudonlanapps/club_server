"""A member's own view of their credit (#294).

Follows the ``/myevents`` and ``/mygroups`` convention: the username is in
the path and ``require_self_or_staff`` guards it, so one endpoint serves
both the member reading their own credit and staff reading it on their
behalf (R15, R16, R86-R88).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.credit_account import CreditAccount
from ..db.models.credit_entry import CreditEntry
from ..db.models.user import User
from ..dependencies import (
    get_current_active_user,
    get_db,
    require_credit_system_enabled,
    require_self_or_staff,
)
from ..exceptions import CreditAccountNotFoundException
from ..schemas.common import PaginatedResponse
from ..schemas.credit import CreditAccountResponse, CreditEntryResponse
from ..services.credit import CreditService
from ..services.credit_statement import StatementOrder, page_statement
from ..utils import now_utc_ms
from .credits import to_response

router = APIRouter(
    prefix="/mycredits",
    tags=["My Credits"],
    dependencies=[Depends(require_credit_system_enabled)],
)


@router.get("/by_id/{username}", response_model=list[CreditAccountResponse])
async def list_my_accounts(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    state: str | None = Query(None),
    include_closed: bool = Query(False, alias="includeClosed"),
) -> list[CreditAccountResponse]:
    """List a member's credit accounts.

    Expired and empty accounts are listed alongside live ones and described
    as such (R88) — an expired balance is never hidden from the member who
    owns it. Closed accounts are excluded unless asked for, since they hold
    nothing and only clutter the common case.
    """
    require_self_or_staff(username, current_user)
    result = await db.execute(
        select(CreditAccount)
        .where(CreditAccount.membername == username)
        .order_by(CreditAccount.opened_at, CreditAccount.id)
    )
    service = CreditService(db)
    views = [await service.view(account) for account in result.scalars().all()]
    now = now_utc_ms()
    if not include_closed:
        views = [v for v in views if v.account.closed_at is None]
    if state is not None:
        views = [v for v in views if v.account.state(v.balance, now) == state]
    return [to_response(view) for view in views]


@router.get(
    "/by_id/{username}/accounts/{account_id}", response_model=CreditAccountResponse
)
async def get_my_account(
    username: str,
    account_id: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CreditAccountResponse:
    """Read one of a member's own accounts."""
    require_self_or_staff(username, current_user)
    service = CreditService(db)
    try:
        account = await service.get_account_or_raise(account_id)
    except CreditAccountNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CREDIT_ACCOUNT_NOT_FOUND", "message": str(e)},
        )
    if account.membername != username:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CREDIT_ACCOUNT_NOT_FOUND",
                "message": f"No credit account with id {account_id}",
            },
        )
    return to_response(await service.view(account))


@router.get(
    "/by_id/{username}/entries", response_model=PaginatedResponse[CreditEntryResponse]
)
async def list_my_entries(
    username: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_current_active_user)],
    account_id: str | None = Query(None, alias="accountId"),
    event_id: int | None = Query(None, alias="eventId"),
    from_ts: int | None = Query(None, alias="fromTs"),
    to_ts: int | None = Query(None, alias="toTs"),
    order: StatementOrder = Query("asc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[CreditEntryResponse]:
    """A member's statement: what each session cost and which account paid.

    Where a cost was split across accounts (R29) each part appears as its
    own entry, so the statement adds up to what was actually taken. Each
    entry carries the account's balance and the member's total after it
    (#449), so a page read newest first still shows what was left.
    """
    require_self_or_staff(username, current_user)
    stmt: Select[tuple[CreditEntry]] = (
        select(CreditEntry)
        .join(CreditAccount, CreditAccount.id == CreditEntry.account_id)
        .where(CreditAccount.membername == username)
    )
    if account_id is not None:
        stmt = stmt.where(CreditAccount.code == account_id)
    if event_id is not None:
        stmt = stmt.where(CreditEntry.event_id == event_id)
    if from_ts is not None:
        stmt = stmt.where(CreditEntry.created_at >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(CreditEntry.created_at <= to_ts)

    return await page_statement(
        db, stmt, membername=username, order=order, offset=offset, limit=limit
    )
