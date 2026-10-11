"""Running the scheduler loop for exactly one tick in a test (#34)."""

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from club_server.services.scheduler import scheduler_loop

FIRST_TICK_TIMEOUT_SECONDS = 60.0
"""How long a test waits for the loop's first tick before giving up."""


class _TickSession:
    """A tick's session that reports when the loop has closed it."""

    def __init__(self, session: AsyncSession, closed: asyncio.Event) -> None:
        self._session = session
        self._closed = closed

    async def __aenter__(self) -> AsyncSession:
        return await self._session.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> None:
        try:
            await self._session.__aexit__(*exc_info)
        finally:
            self._closed.set()


class _TickSignal:
    """A session factory that says when a tick's scans have all finished.

    The loop opens one session per tick and closes it after the last scan,
    so the close is the end of the tick however long the scans took.
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.tick_done = asyncio.Event()

    def __call__(self) -> _TickSession:
        return _TickSession(self._session_factory(), self.tick_done)


async def run_loop_once(session_factory, tick_seconds: float = 1.0) -> None:
    """Start the loop, wait for its first tick to finish, cancel it (#34)."""
    signal = _TickSignal(session_factory)
    task = asyncio.create_task(scheduler_loop(signal, tick_seconds=tick_seconds))
    try:
        await asyncio.wait_for(
            signal.tick_done.wait(), timeout=FIRST_TICK_TIMEOUT_SECONDS
        )
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
