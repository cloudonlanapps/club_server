"""Credit accounts and the ledger they derive from (#294).

A member does not have a balance; they have accounts, and a balance is the
sum of an account's ledger entries. Nothing here mutates a balance — every
change is a new entry — which is what makes the ledger's immutability (R80)
a property of the code rather than a rule to remember.
"""

import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.credit_account import ACCOUNT_CODE_LENGTH, CreditAccount
from ..db.models.credit_entry import CreditEntry, CreditEntryType
from ..db.models.event import Event
from ..db.models.user import User
from ..exceptions import (
    CreditAccountNotFoundException,
    CreditNotApplicableException,
    InvalidCreditAmountException,
    InvalidValidityWindowException,
    SuperAdminCannotHoldCreditException,
    UserNotFoundException,
)
from ..utils import now_utc_ms
from .event_types import is_programme

# Unambiguous alphabet: no O/0, I/1, or S/5, so a member reading a code down
# the phone and an admin typing it in cannot disagree about it.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXYZ23456789"
_CODE_ATTEMPTS = 20


@dataclass(frozen=True)
class AccountView:
    """An account together with the balance derived for it."""

    account: CreditAccount
    balance: int


class CreditService:
    """Account lifecycle and ledger reads."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    # --- lookups -------------------------------------------------------

    async def get_account_or_raise(
        self, code: str, *, for_update: bool = False
    ) -> CreditAccount:
        """Fetch one account by its 8-character code (R91).

        ``for_update`` takes the row lock (``SELECT ... FOR UPDATE``) and
        refreshes the loaded row, for a caller about to read the balance and
        write against it (#477, R12, R31). Plain reads leave it alone.
        """
        statement = select(CreditAccount).where(CreditAccount.code == code)
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        result = await self.db.execute(statement)
        account = result.scalar_one_or_none()
        if account is None:
            raise CreditAccountNotFoundException(code)
        return account

    async def balance_of(self, account_id: int) -> int:
        """Sum the ledger for one account (R4)."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(CreditEntry.amount), 0)).where(
                CreditEntry.account_id == account_id
            )
        )
        return int(result.scalar_one())

    async def lock_accounts(self, account_ids: list[int]) -> None:
        """Take the row lock on these accounts, in id order (#477).

        Every path that changes a balance holds it for the rest of its
        transaction, so a concurrent charge, reversal or transfer waits for
        it instead of reading a balance that is about to change. Locking in
        id order keeps two such paths from deadlocking on each other.
        """
        if not account_ids:
            return
        _ = await self.db.execute(
            select(CreditAccount.id)
            .where(CreditAccount.id.in_(sorted(set(account_ids))))
            .order_by(CreditAccount.id)
            .with_for_update()
        )

    async def view(self, account: CreditAccount) -> AccountView:
        """Pair an account with its current balance."""
        return AccountView(account=account, balance=await self.balance_of(account.id))

    async def bound_balance(self, membername: str, root_event_id: int) -> int:
        """Total unresolved credit a member holds on one programme."""
        result = await self.db.execute(
            select(CreditAccount).where(
                CreditAccount.membername == membername,
                CreditAccount.event_id == root_event_id,
                CreditAccount.closed_at.is_(None),
            )
        )
        total = 0
        for account in result.scalars().all():
            total += await self.balance_of(account.id)
        return total

    async def programme_id(self, event_id: int) -> int:
        """Check ``event_id`` names a programme — the only type credit applies to (R6).

        An event keeps one id for life (``event_schedule_model.md``), so the
        id itself is the stable identity a bound account carries (R7).
        """
        event = await self._get_event_or_raise(event_id)
        if not is_programme(event):
            raise CreditNotApplicableException(event_id, event.type)
        return event.id

    async def _get_event_or_raise(self, event_id: int) -> Event:
        event = await self.db.get(Event, event_id)
        if event is None:
            raise CreditNotApplicableException(event_id, "unknown")
        return event

    async def _require_user(self, membername: str) -> User:
        result = await self.db.execute(select(User).where(User.username == membername))
        user = result.scalar_one_or_none()
        if user is None:
            raise UserNotFoundException(membername)
        return user

    # --- opening -------------------------------------------------------

    async def open_account(
        self,
        *,
        membername: str,
        credits: int,
        valid_from: int,
        valid_until: int,
        reason: str,
        actor: str,
        event_id: int | None = None,
        is_trial: bool = False,
    ) -> AccountView:
        """Open an account and record its opening grant (R18-R24).

        Never used to top an account up: more credit means another account
        (R20), so this always inserts rather than adding to anything.
        """
        user = await self._require_user(membername)
        if user.is_super_admin:
            raise SuperAdminCannotHoldCreditException(membername)
        if credits <= 0:
            raise InvalidCreditAmountException(credits)
        self._validate_window(valid_from, valid_until)

        root_id = None if event_id is None else await self.programme_id(event_id)

        now = now_utc_ms()
        account = CreditAccount(
            code=await self._new_code(),
            membername=membername,
            event_id=root_id,
            is_trial=is_trial,
            valid_from=valid_from,
            valid_until=valid_until,
            opened_at=now,
            opened_by=actor,
        )
        self.db.add(account)
        await self.db.flush()

        _ = self._add_entry(
            account=account,
            amount=credits,
            entry_type=CreditEntryType.grant,
            reason=reason,
            actor=actor,
            event_id=root_id,
        )
        await self.db.flush()
        return AccountView(account=account, balance=credits)

    def _validate_window(
        self, valid_from: int, valid_until: int, *, check_clock: bool = True
    ) -> None:
        """A window must end after it starts and must not have already closed (R23).

        ``check_clock=False`` skips the second rule for a window that was
        checked when it was stated and is only now being applied (#485).
        """
        if valid_until <= valid_from:
            raise InvalidValidityWindowException(
                "Validity window must end after it starts"
            )
        if check_clock and valid_until <= now_utc_ms():
            raise InvalidValidityWindowException("Validity window has already closed")

    async def _new_code(self) -> str:
        """Generate an unused account code (R5)."""
        for _ in range(_CODE_ATTEMPTS):
            code = "".join(
                secrets.choice(_CODE_ALPHABET) for _ in range(ACCOUNT_CODE_LENGTH)
            )
            existing = await self.db.execute(
                select(CreditAccount.id).where(CreditAccount.code == code)
            )
            if existing.scalar_one_or_none() is None:
                return code
        raise RuntimeError("Could not generate an unused credit account code")

    def _add_entry(
        self,
        *,
        account: CreditAccount,
        amount: int,
        entry_type: CreditEntryType,
        reason: str,
        actor: str | None,
        event_id: int | None = None,
        occurrence_time_utc: int | None = None,
        charge_id: int | None = None,
        offsets_entry_id: int | None = None,
    ) -> CreditEntry:
        """Append one movement to the ledger. Entries are never updated (R80)."""
        entry = CreditEntry(
            account_id=account.id,
            amount=amount,
            entry_type=entry_type.value,
            event_id=event_id,
            occurrence_time_utc=occurrence_time_utc,
            charge_id=charge_id,
            reason=reason,
            actor_username=actor,
            created_at=now_utc_ms(),
            offsets_entry_id=offsets_entry_id,
        )
        self.db.add(entry)
        return entry
