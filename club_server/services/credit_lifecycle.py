"""Operations that change a credit account after it is opened (#294).

Extending a validity window, reversing a mistaken grant, transferring a
balance out, and reopening a closed account a refund lands in. Split from ``credit.py`` — which covers lookups, balances and
opening — to keep both files within the repo's size limit.

Nothing here mutates a balance. Every change is a new ledger entry, so the
immutability of the ledger (R80) is a property of the code rather than a
rule someone has to remember. Every operation locks the account row before
reading its balance (#477), as spending does (R31).
"""

from sqlalchemy import select

from ..db.models.credit_account import CreditAccount
from ..db.models.credit_entry import CreditEntry, CreditEntryType
from ..exceptions import (
    CreditAccountClosedException,
    InsufficientBalanceException,
    InvalidCreditAmountException,
    InvalidValidityWindowException,
)
from ..utils import now_utc_ms
from .audit import AuditService
from .audit_actions import SYSTEM_ACTOR, AuditAction
from .credit import AccountView, CreditService

REOPENED_BY_REFUND = "sessionRefund"
"""The ``reason`` a reopening records when a session refund caused it (#479)."""


class CreditLifecycleService(CreditService):
    """Corrections, expiry disposition and departures."""

    async def extend_validity(
        self, *, code: str, valid_until: int, reason: str, actor: str
    ) -> AccountView:
        """Move an account's validity end forwards (R62).

        The only route back from expiry, and the reason expired credit is
        never destroyed: there is always something left to extend (R61).
        """
        account = await self.get_account_or_raise(code, for_update=True)
        self._require_open(account)
        if valid_until <= account.valid_until:
            raise InvalidValidityWindowException(
                "An extension must move the end date forwards"
            )
        account.valid_until = valid_until
        _ = self._add_entry(
            account=account,
            amount=0,
            entry_type=CreditEntryType.validity_extended,
            reason=reason,
            actor=actor,
            event_id=account.event_id,
        )
        await self.db.flush()
        return await self.view(account)

    async def reverse_grant(
        self, *, code: str, reason: str, actor: str, credits: int | None = None
    ) -> AccountView:
        """Undo an admin's own mistaken grant (R58, R59).

        A reversal is a new offsetting entry, not an edit, and it can never
        reach past what remains unspent — spent credit is addressed through
        the attendance record that spent it.
        """
        account = await self.get_account_or_raise(code, for_update=True)
        self._require_open(account)
        balance = await self.balance_of(account.id)
        amount = balance if credits is None else credits
        if amount <= 0:
            raise InvalidCreditAmountException(amount)
        if amount > balance:
            raise InsufficientBalanceException(code, balance, amount)

        grant = await self.db.execute(
            select(CreditEntry.id)
            .where(
                CreditEntry.account_id == account.id,
                CreditEntry.entry_type == CreditEntryType.grant.value,
            )
            .order_by(CreditEntry.id)
            .limit(1)
        )
        _ = self._add_entry(
            account=account,
            amount=-amount,
            entry_type=CreditEntryType.grant_reversal,
            reason=reason,
            actor=actor,
            event_id=account.event_id,
            offsets_entry_id=grant.scalar_one_or_none(),
        )
        await self.db.flush()
        return await self.view(account)

    async def transfer(
        self,
        *,
        code: str,
        penalty: int,
        valid_from: int,
        valid_until: int,
        reason: str,
        actor: str | None,
        check_clock: bool = True,
    ) -> tuple[AccountView, AccountView | None]:
        """Close an account, moving what survives the penalty into a new one.

        One mechanic serves three stories — expiry disposition, voluntary
        drop-out and admin removal — which differ only in the penalty (R63,
        R65-R69). There is no destination to choose: accounts are never
        topped up, so the only lawful destination is one this creates.

        A penalty larger than the balance stops at zero. The shortfall is
        not carried anywhere; the member's other accounts are not consulted
        (R69).

        ``check_clock=False`` applies a window already checked when it was
        stated, even if it has closed since (#485).
        """
        account = await self.get_account_or_raise(code, for_update=True)
        self._require_open(account)
        balance = await self.balance_of(account.id)
        applied_penalty = min(penalty, balance)
        moving = balance - applied_penalty
        now = now_utc_ms()

        if applied_penalty > 0:
            _ = self._add_entry(
                account=account,
                amount=-applied_penalty,
                entry_type=CreditEntryType.penalty,
                reason=reason,
                actor=actor,
                event_id=account.event_id,
            )

        created: AccountView | None = None
        if moving > 0:
            self._validate_window(valid_from, valid_until, check_clock=check_clock)
            _ = self._add_entry(
                account=account,
                amount=-moving,
                entry_type=CreditEntryType.transfer_out,
                reason=reason,
                actor=actor,
                event_id=account.event_id,
            )
            destination = CreditAccount(
                code=await self._new_code(),
                membername=account.membername,
                event_id=None,
                is_trial=False,
                valid_from=valid_from,
                valid_until=valid_until,
                opened_at=now,
                opened_by=actor,
            )
            self.db.add(destination)
            await self.db.flush()
            _ = self._add_entry(
                account=destination,
                amount=moving,
                entry_type=CreditEntryType.transfer_in,
                reason=reason,
                actor=actor,
            )
            created = AccountView(account=destination, balance=moving)

        account.closed_at = now
        await self.db.flush()
        return AccountView(account=account, balance=0), created

    async def reopen_for_refund(
        self,
        account_ids: list[int],
        *,
        event_id: int,
        occurrence_time_utc: int,
        actor: str | None,
    ) -> None:
        """Reopen the closed accounts a session refund lands in (R56, #479).

        The refund reaches its source account whatever happened to it
        since (R56). A departure's transfer closes that account, so the
        refund clears the closure and audits it; the credit can then be
        spent, extended or transferred again. The caller holds the row locks.
        """
        if not account_ids:
            return
        result = await self.db.execute(
            select(CreditAccount)
            .where(
                CreditAccount.id.in_(set(account_ids)),
                CreditAccount.closed_at.is_not(None),
            )
            .order_by(CreditAccount.id)
        )
        audit = AuditService(self.db)
        for account in result.scalars().all():
            closed_at = account.closed_at
            account.closed_at = None
            await audit.log(
                actor_username=actor or SYSTEM_ACTOR,
                action=AuditAction.REOPEN_CREDIT_ACCOUNT,
                target_username=account.membername,
                resource_type="credit_account",
                resource_id=account.code,
                details={
                    "reason": REOPENED_BY_REFUND,
                    "eventId": event_id,
                    "occurrenceTimeUtc": occurrence_time_utc,
                    "closedAtUtc": closed_at,
                },
            )
        await self.db.flush()

    def _require_open(self, account: CreditAccount) -> None:
        """An account is drained once; a closed one accepts nothing further."""
        if account.closed_at is not None:
            raise CreditAccountClosedException(account.code)

    async def dispose_bound_credit(
        self,
        *,
        membername: str,
        root_event_id: int,
        penalty: int,
        valid_from: int,
        valid_until: int,
        reason: str,
        actor: str | None,
        check_clock: bool = True,
    ) -> list[AccountView]:
        """Resolve every balance a departing member holds on one programme.

        The penalty is a single flat figure for the departure, not a figure
        per account: it is taken from the member's bound accounts oldest
        first until it is used up, and whatever survives moves into new
        general accounts (R65-R69). Charging the full penalty once per
        account would punish a member for having bought two packages
        instead of one.

        Expired bound accounts are included. Expiry never destroys credit
        (R61), so a lapsed balance is exactly the kind that would otherwise
        be stranded by a removal.

        The sweep passes ``check_clock=False`` for a deferred disposition:
        its window was checked when the admin stated it, and is applied as
        stated even if it has closed by settlement time (#485).
        """
        result = await self.db.execute(
            select(CreditAccount)
            .where(
                CreditAccount.membername == membername,
                CreditAccount.event_id == root_event_id,
                CreditAccount.closed_at.is_(None),
            )
            .order_by(CreditAccount.opened_at, CreditAccount.id)
            .with_for_update()
        )
        remaining_penalty = penalty
        created: list[AccountView] = []
        for account in result.scalars().all():
            balance = await self.balance_of(account.id)
            if balance <= 0:
                continue
            applied = min(remaining_penalty, balance)
            remaining_penalty -= applied
            _, new_account = await self.transfer(
                code=account.code,
                penalty=applied,
                valid_from=valid_from,
                valid_until=valid_until,
                reason=reason,
                actor=actor,
                check_clock=check_clock,
            )
            if new_account is not None:
                created.append(new_account)
        return created
