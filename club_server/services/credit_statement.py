"""Reading the ledger as a statement (#449, R87).

Both entry listings — a member's own and the staff ledger — page in SQL and
give each entry two running figures, computed over the whole ledger before
any filter or page is applied, so an entry reads the same wherever it shows:

- ``balance_after``: its account's balance after it;
- ``total_after``: the member's credit across all their accounts after it.

A transfer moves credit between two accounts of the same member (R65-R68),
so ``transferOut`` and ``transferIn`` leave the member's total unchanged and
are left out of ``total_after``. Summing them would show the total dipping
to nothing between the two halves of one transfer.
"""

from typing import Literal

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.credit_account import CreditAccount
from ..db.models.credit_entry import CreditEntry, CreditEntryType
from ..schemas.common import PaginatedResponse
from ..schemas.credit import CreditEntryResponse

StatementOrder = Literal["asc", "desc"]

TRANSFER_TYPES = (
    CreditEntryType.transfer_out.value,
    CreditEntryType.transfer_in.value,
)


def _running_figures(membername: str | None):
    """Each entry's running account balance and running member total.

    Restricting to one member changes no figure — both windows partition by
    account or member — it only spares the scan of everyone else's ledger.
    """
    ordering = (CreditEntry.created_at, CreditEntry.id)
    movement = case(
        (CreditEntry.entry_type.in_(TRANSFER_TYPES), 0), else_=CreditEntry.amount
    )
    stmt = select(
        CreditEntry.id.label("entry_id"),
        func.sum(CreditEntry.amount)
        .over(partition_by=CreditEntry.account_id, order_by=ordering)
        .label("balance_after"),
        func.sum(movement)
        .over(partition_by=CreditAccount.membername, order_by=ordering)
        .label("total_after"),
    ).join(CreditAccount, CreditAccount.id == CreditEntry.account_id)
    if membername is not None:
        stmt = stmt.where(CreditAccount.membername == membername)
    return stmt.subquery()


async def page_statement(
    db: AsyncSession,
    stmt: Select[tuple[CreditEntry]],
    *,
    membername: str | None,
    order: StatementOrder,
    offset: int,
    limit: int,
) -> PaginatedResponse[CreditEntryResponse]:
    """One page of ``stmt``'s entries, with their running figures.

    ``stmt`` selects entries joined to their account and carries the
    listing's filters. ``membername``, when the listing is for one member,
    narrows the running-figure scan to that member.
    """
    count = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = int(count.scalar_one())

    figures = _running_figures(membername)
    if order == "desc":
        ordering = (CreditEntry.created_at.desc(), CreditEntry.id.desc())
    else:
        ordering = (CreditEntry.created_at, CreditEntry.id)
    page = await db.execute(
        stmt.join(figures, figures.c.entry_id == CreditEntry.id)
        .add_columns(
            CreditAccount.code,
            CreditAccount.membername,
            figures.c.balance_after,
            figures.c.total_after,
        )
        .order_by(*ordering)
        .offset(offset)
        .limit(limit)
    )

    items = [
        CreditEntryResponse(
            id=entry.id,
            account_id=code,
            membername=owner,
            amount=entry.amount,
            entry_type=entry.entry_type,
            event_id=entry.event_id,
            occurrence_time_utc=entry.occurrence_time_utc,
            reason=entry.reason,
            actor_username=entry.actor_username,
            created_at_utc=entry.created_at,
            offsets_entry_id=entry.offsets_entry_id,
            balance_after=int(balance_after),
            total_after=int(total_after),
        )
        for entry, code, owner, balance_after, total_after in page.all()
    ]
    return PaginatedResponse[CreditEntryResponse](
        items=items, total=total, offset=offset, limit=limit
    )
