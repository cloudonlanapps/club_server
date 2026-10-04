"""Deciding which account pays (#294).

There is no default account and no setting that nominates one. Which
account pays is derived every time, from the accounts' own state:
event-bound before general (R25, R26), oldest before newest within each
kind (R27), skipping anything expired, empty or closed (R28).

Trial and ordinary credit never mix in either direction (R53).
"""

from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.credit_account import CreditAccount
from ..db.models.event import Event
from .credit import AccountView, CreditService
from ..utils import now_utc_ms

# The cost of one occurrence. Today every programme charges one credit per
# session (R33). The value is read through `session_cost_for` rather than
# written inline anywhere, so moving it onto the event row later is a change
# to this one function (R32, R34).
DEFAULT_SESSION_COST = 1


def session_cost_for(event: Event) -> int:
    """Credits consumed by one occurrence of ``event``.

    Always 1 today. Every rule that spends, refunds or checks credit is
    expressed in terms of this, never a literal, so that giving events
    their own price later touches nothing but this function.
    """
    _ = event
    return DEFAULT_SESSION_COST


@dataclass(frozen=True)
class Allocation:
    """One account's share of a cost."""

    account: CreditAccount
    amount: int


class CreditSelection:
    """Reads the accounts that could pay, in the order they would be used."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db
        self._credits: CreditService = CreditService(db)

    def accounts_query(
        self, membername: str, *, is_trial: bool
    ) -> "Select[tuple[CreditAccount]]":
        """The member's open accounts of one kind, oldest first."""
        return (
            select(CreditAccount)
            .where(
                CreditAccount.membername == membername,
                CreditAccount.closed_at.is_(None),
                CreditAccount.is_trial == is_trial,
            )
            .order_by(CreditAccount.opened_at, CreditAccount.id)
        )

    def spending_query(
        self, membername: str, root_event_id: int | None, *, is_trial: bool
    ) -> "Select[tuple[CreditAccount]]":
        """The same query, holding a row lock for the length of the spend.

        Deciding what to deduct means reading a balance and then writing
        against it. Without the lock two concurrent attendance writes can
        each read the same balance and each spend the last credit, taking it
        negative — which R12 forbids and no unique constraint catches,
        because they are two different occurrences (R31).
        """
        _ = root_event_id
        return self.accounts_query(membername, is_trial=is_trial).with_for_update()

    async def usable_accounts(
        self,
        membername: str,
        root_event_id: int | None,
        *,
        is_trial: bool,
        for_update: bool = False,
    ) -> list[AccountView]:
        """Accounts that could pay, in selection order (R25-R28).

        ``root_event_id`` is the id of the programme in question;
        pass ``None`` to consider only general accounts. Set ``for_update``
        when the caller is about to spend, so the rows are locked while it
        decides (R31); plain reads leave them alone.
        """
        statement = (
            self.spending_query(membername, root_event_id, is_trial=is_trial)
            if for_update
            else self.accounts_query(membername, is_trial=is_trial)
        )
        result = await self.db.execute(statement)
        now = now_utc_ms()
        bound: list[AccountView] = []
        general: list[AccountView] = []
        for account in result.scalars().all():
            if account.event_id is not None and account.event_id != root_event_id:
                continue
            view = await self._credits.view(account)
            if not account.is_usable(view.balance, now):
                continue
            (bound if account.event_id is not None else general).append(view)
        # A usable event-bound account is never bypassed in favour of a
        # general one, whatever their relative ages or balances (R26).
        return bound + general

    async def usable_credits(
        self, membername: str, root_event_id: int | None, *, is_trial: bool = False
    ) -> int:
        """Total credit this member could spend on this programme."""
        views = await self.usable_accounts(membername, root_event_id, is_trial=is_trial)
        return sum(view.balance for view in views)

    async def allocate(
        self,
        membername: str,
        root_event_id: int | None,
        cost: int,
        *,
        is_trial: bool = False,
    ) -> list[Allocation] | None:
        """Split ``cost`` across accounts in selection order (R29, R30).

        Returns ``None`` when the usable accounts cannot meet the cost in
        full: the whole cost is taken or none of it is, so a caller never
        has to unwind a partial spend.
        """
        remaining = cost
        allocations: list[Allocation] = []
        for view in await self.usable_accounts(
            membername, root_event_id, is_trial=is_trial, for_update=True
        ):
            if remaining <= 0:
                break
            take = min(view.balance, remaining)
            allocations.append(Allocation(account=view.account, amount=take))
            remaining -= take
        if remaining > 0:
            return None
        return allocations

    async def next_expiry(
        self, membername: str, root_event_id: int | None, *, is_trial: bool = False
    ) -> int | None:
        """When this member's usable credit for the programme first lapses."""
        views = await self.usable_accounts(membername, root_event_id, is_trial=is_trial)
        if not views:
            return None
        return min(view.account.valid_until for view in views)
