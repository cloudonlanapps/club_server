"""Reading credit accounts and the ledger (#294).

Staff-facing search and ledger queries. The lifecycle operations live in
``credits.py`` and the member's own view in ``mycredits.py``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.credit_account import CreditAccount, CreditAccountState
from ..db.models.credit_entry import CreditEntry
from ..db.models.user import User
from ..dependencies import (
    get_db,
    require_admin,
    require_admin_or_coach,
    require_credit_system_enabled,
)
from ..exceptions import CreditAccountNotFoundException
from ..schemas.common import PaginatedResponse
from ..schemas.credit import CreditAccountResponse, CreditEntryResponse
from ..services.credit import CreditService
from ..services.credit_statement import StatementOrder, page_statement
from ..utils import now_utc_ms
from .credits import to_response

router = APIRouter(
    prefix="/credits",
    tags=["Credits"],
    dependencies=[Depends(require_credit_system_enabled)],
)


@router.get("/accounts", response_model=PaginatedResponse[CreditAccountResponse])
async def list_accounts(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
    membername: Annotated[str | None, Query()] = None,
    event_id: Annotated[int | None, Query(alias="eventId")] = None,
    kind: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    is_trial: Annotated[bool | None, Query(alias="isTrial")] = None,
    expiring_before_utc: Annotated[int | None, Query(alias="expiringBeforeUtc")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedResponse[CreditAccountResponse]:
    """Search credit accounts (admin or coach).

    ``state`` is derived from the balance and the validity window, so it is
    applied after the rows are read rather than in SQL.
    """
    stmt: Select[CreditAccount] = select(CreditAccount)
    if membername is not None:
        stmt = stmt.where(CreditAccount.membername == membername)
    if event_id is not None:
        stmt = stmt.where(CreditAccount.event_id == event_id)
    if kind == "general":
        stmt = stmt.where(CreditAccount.event_id.is_(None))
    elif kind == "event":
        stmt = stmt.where(CreditAccount.event_id.is_not(None))
    if is_trial is not None:
        stmt = stmt.where(CreditAccount.is_trial == is_trial)
    if expiring_before_utc is not None:
        stmt = stmt.where(CreditAccount.valid_until <= expiring_before_utc)

    result = await db.execute(stmt.order_by(CreditAccount.opened_at, CreditAccount.id))
    accounts = list(result.scalars().all())

    service = CreditService(db)
    views = [await service.view(account) for account in accounts]
    now = now_utc_ms()
    if state is not None:
        views = [v for v in views if v.account.state(v.balance, now) == state]

    total = len(views)
    page = views[offset : offset + limit]
    return PaginatedResponse[CreditAccountResponse](
        items=[to_response(view) for view in page],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/accounts/{account_id}", response_model=CreditAccountResponse)
async def get_account(
    account_id: str,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin())],
) -> CreditAccountResponse:
    """Find one account by its code alone (admin only).

    Admin-only deliberately: this is the endpoint that turns a code quoted
    over the phone into an account, without the caller knowing whose it is.
    """
    service = CreditService(db)
    try:
        account = await service.get_account_or_raise(account_id)
    except CreditAccountNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CREDIT_ACCOUNT_NOT_FOUND", "message": str(e)},
        )
    return to_response(await service.view(account))


@router.get("/entries", response_model=PaginatedResponse[CreditEntryResponse])
async def list_entries(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    _current_user: Annotated[User, Depends(require_admin_or_coach())],
    membername: Annotated[str | None, Query()] = None,
    account_id: Annotated[str | None, Query(alias="accountId")] = None,
    event_id: Annotated[int | None, Query(alias="eventId")] = None,
    entry_type: Annotated[str | None, Query(alias="entryType")] = None,
    occurrence_time_utc: Annotated[int | None, Query(alias="occurrenceTimeUtc")] = None,
    from_ts: Annotated[int | None, Query(alias="fromTs")] = None,
    to_ts: Annotated[int | None, Query(alias="toTs")] = None,
    order: Annotated[StatementOrder, Query()] = "asc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedResponse[CreditEntryResponse]:
    """Read the ledger across accounts (admin or coach).

    Each entry carries its running ``balanceAfter`` and ``totalAfter``,
    which no filter or page changes (#449).
    """
    stmt: Select[CreditEntry] = select(CreditEntry).join(
        CreditAccount, CreditAccount.id == CreditEntry.account_id
    )
    if membername is not None:
        stmt = stmt.where(CreditAccount.membername == membername)
    if account_id is not None:
        stmt = stmt.where(CreditAccount.code == account_id)
    if event_id is not None:
        stmt = stmt.where(CreditEntry.event_id == event_id)
    if entry_type is not None:
        stmt = stmt.where(CreditEntry.entry_type == entry_type)
    if occurrence_time_utc is not None:
        stmt = stmt.where(CreditEntry.occurrence_time_utc == occurrence_time_utc)
    if from_ts is not None:
        stmt = stmt.where(CreditEntry.created_at >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(CreditEntry.created_at <= to_ts)

    return await page_statement(
        db, stmt, membername=membername, order=order, offset=offset, limit=limit
    )


__all__ = ["router", "CreditAccountState"]
