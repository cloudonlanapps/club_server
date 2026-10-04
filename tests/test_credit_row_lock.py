"""Balance-changing credit operations lock the account row (#477).

A reversal or a transfer reads an account's balance and then writes against
it. Without ``SELECT ... FOR UPDATE`` an attendance charge committing in
between lets both succeed and takes the balance negative (R12, R31).

A true race is not reproducible deterministically, so these tests prove the
lock itself, two ways. The statement log shows the ``FOR UPDATE`` on the
account is issued before the balance is summed. And while one session holds
an uncommitted reversal, a second session asking for the row with
``FOR NO KEY UPDATE NOWAIT`` is refused. That probe, unlike a plain
``FOR UPDATE``, does not collide with the key-share lock the ledger insert's
foreign key takes, so it fails only on a real row lock; a plain read, the
last test, leaves it free.
"""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from club_server.db.models.credit_account import CreditAccount
from club_server.services.credit import CreditService
from club_server.services.credit_lifecycle import CreditLifecycleService

from .credit_helpers import create_member, days_from_now
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


@pytest_asyncio.fixture(scope="function")
async def other_session(test_engine):
    """A second, independent connection to the same database."""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()


async def _open(db_session: AsyncSession) -> CreditAccount:
    _ = await create_admin_user(db_session)
    _ = await create_member(db_session, "alice")
    view = await CreditService(db_session).open_account(
        membername="alice",
        credits=10,
        valid_from=days_from_now(-1),
        valid_until=days_from_now(30),
        reason="Package",
        actor="admin",
    )
    await db_session.commit()
    return view.account


async def _row_is_locked(session: AsyncSession, account_id: int) -> bool:
    try:
        _ = await session.execute(
            select(CreditAccount.id)
            .where(CreditAccount.id == account_id)
            .with_for_update(nowait=True, key_share=True)
        )
    except DBAPIError:
        await session.rollback()
        return True
    await session.rollback()
    return False


@pytest.fixture(scope="function")
def statements(test_engine):
    """Every SQL statement the engine executes during the test, in order."""
    seen: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _many) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(test_engine.sync_engine, "before_cursor_execute", _record)
    yield seen
    event.remove(test_engine.sync_engine, "before_cursor_execute", _record)


def _locked_before_balance_read(seen: list[str]) -> bool:
    lock = next(
        (
            i
            for i, sql in enumerate(seen)
            if "FROM credit_accounts" in sql and sql.endswith("FOR UPDATE")
        ),
        None,
    )
    read = next(
        (i for i, sql in enumerate(seen) if "sum(credit_entries.amount)" in sql),
        None,
    )
    return lock is not None and read is not None and lock < read


@pytest.mark.asyncio
async def test_should_lock_account_row_when_reversing_grant(
    db_session: AsyncSession, other_session: AsyncSession, statements: list[str]
):
    account = await _open(db_session)
    statements.clear()

    view = await CreditLifecycleService(db_session).reverse_grant(
        code=account.code, reason="Wrong member", actor="admin", credits=4
    )
    assert view.balance == 6

    assert _locked_before_balance_read(statements)
    assert await _row_is_locked(other_session, account.id)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_should_lock_account_row_when_transferring(
    db_session: AsyncSession, statements: list[str]
):
    account = await _open(db_session)
    statements.clear()

    source, created = await CreditLifecycleService(db_session).transfer(
        code=account.code,
        penalty=1,
        valid_from=days_from_now(-1),
        valid_until=days_from_now(60),
        reason="Left",
        actor="admin",
    )
    assert source.balance == 0
    assert created is not None and created.balance == 9

    assert _locked_before_balance_read(statements)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_should_not_lock_account_row_when_only_reading_it(
    db_session: AsyncSession, other_session: AsyncSession
):
    account = await _open(db_session)

    view = await CreditService(db_session).view(
        await CreditService(db_session).get_account_or_raise(account.code)
    )
    assert view.balance == 10

    assert not await _row_is_locked(other_session, account.id)
    await db_session.rollback()
