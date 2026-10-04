"""Ending a login session at logout (#510).

A session begins at login: the access and refresh tokens issued there carry
one ``sid``, and ``/auth/refresh`` passes it on to every pair it issues.
Logout records the session as revoked, and every token carrying its ``sid``
is refused from then on. Other sessions of the same user are untouched.

Tokens issued before #510 carry no ``sid``; logout cannot name their
session, so they keep working until they expire.
"""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.revoked_session import RevokedSession
from ..schemas.auth import TokenPayload
from ..utils import now_utc_ms
from .auth import REFRESH_TOKEN_LIFETIME


def _ms(delta: timedelta) -> int:
    return int(delta.total_seconds() * 1000)


class AuthSessionService:
    """Revokes login sessions and answers whether a token's session ended."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def is_revoked(self, payload: TokenPayload) -> bool:
        """True if the token belongs to a session ended by logout."""
        if payload.sid is None:
            return False
        result = await self.db.execute(
            select(RevokedSession.session_id).where(
                RevokedSession.session_id == payload.sid
            )
        )
        return result.scalar_one_or_none() is not None

    async def revoke(self, payload: TokenPayload) -> None:
        """End the session ``payload`` belongs to.

        The row is kept for a refresh token's lifetime: every token of the
        session was issued before now, so none verifies after that. Rows
        past that point are purged here, so the table stays small without
        a separate sweep.
        """
        now = now_utc_ms()
        _ = await self.db.execute(
            delete(RevokedSession).where(RevokedSession.expires_at < now)
        )
        if payload.sid is None:
            return
        _ = await self.db.execute(
            insert(RevokedSession)
            .values(
                session_id=payload.sid,
                username=payload.sub,
                revoked_at=now,
                expires_at=now + _ms(REFRESH_TOKEN_LIFETIME),
            )
            .on_conflict_do_nothing(index_elements=[RevokedSession.session_id])
        )
        await self.db.flush()
