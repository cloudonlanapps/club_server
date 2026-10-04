"""The scheduled credit sweep (#374, #378 — lifecycle L20a, credit R73d).

One idempotent pass, run hourly by the scheduler, with two callers:

- **departures** whose last covered occurrence has passed apply the
  disposition the admin stated when the member left, in that admin's name
  (programme R29d, credit R74a, R82);
- **terminated programmes** whose cutoff has passed release every balance
  still bound to them: each member's unspent credit moves to a general
  account keeping its validity window, with no penalty, and the member is
  notified (programme R10, credit R73a–R73c).

Each departure settles in its own savepoint: a row that fails is logged
and left pending for the next sweep, and the rest settle regardless (#485).

Idempotency is a stamp, not an inference: a departure clears its pending
disposition and a programme records ``credit_released_at``, so a sweep that
runs twice cannot release twice.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.credit_account import CreditAccount
from ..db.models.enrollment import Enrollment
from ..db.models.event import Event
from .audit import AuditService
from .audit_actions import SYSTEM_ACTOR, AuditAction
from .credit_lifecycle import CreditLifecycleService
from .credit_pending import parse_pending_disposition
from .event_types import PROGRAMME
from .notification import NotificationEvent, NotificationService

logger = logging.getLogger(__name__)

# A release names SYSTEM_ACTOR in its audit row, and its ledger entries
# carry no actor: the club ended the programme, and ``actor_username`` is a
# user reference.


async def sweep_credit_settlements(session: AsyncSession, now: int) -> int:
    """Release and settle everything due at ``now``; return the count acted on."""
    if not settings.credit_system_enabled:
        return 0
    acted = await _settle_due_departures(session, now)
    acted += await _release_terminated_programmes(session, now)
    return acted


async def _settle_due_departures(session: AsyncSession, now: int) -> int:
    lifecycle = CreditLifecycleService(session)
    rows = (
        (
            await session.execute(
                select(Enrollment).where(
                    Enrollment.pending_disposition.isnot(None),
                    Enrollment.settle_after_utc <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    settled = 0
    for enrollment in rows:
        # Plain values up front: a rolled-back savepoint expires the row.
        enrollment_id = enrollment.id
        try:
            async with session.begin_nested():
                await _settle_departure(lifecycle, enrollment)
        except Exception:
            logger.exception(
                "credit sweep: settling enrollment %s failed; left pending",
                enrollment_id,
            )
            continue
        settled += 1
    return settled


async def _settle_departure(
    lifecycle: CreditLifecycleService, enrollment: Enrollment
) -> None:
    """Apply one deferred disposition as the admin stated it (R74a, R82).

    Its window was checked when it was stated, so a window that has closed
    by now is applied anyway (#485).
    """
    disposition, actor = parse_pending_disposition(
        enrollment.pending_disposition or "{}"
    )
    _ = await lifecycle.dispose_bound_credit(
        membername=enrollment.membername,
        root_event_id=enrollment.event_id,
        penalty=disposition.penalty,
        valid_from=disposition.valid_from_utc,
        valid_until=disposition.valid_until_utc,
        reason=disposition.reason,
        actor=actor,
        check_clock=False,
    )
    enrollment.pending_disposition = None
    enrollment.settle_after_utc = None


async def _release_terminated_programmes(session: AsyncSession, now: int) -> int:
    lifecycle = CreditLifecycleService(session)
    notifier = NotificationService(session)
    audit = AuditService(session)
    events = (
        (
            await session.execute(
                select(Event).where(
                    Event.type == PROGRAMME,
                    Event.deleted_at.is_(None),
                    Event.credit_released_at.is_(None),
                    Event.cutoff.isnot(None),
                    Event.cutoff <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    released = 0
    for event in events:
        accounts = (
            (
                await session.execute(
                    select(CreditAccount)
                    .where(
                        CreditAccount.event_id == event.id,
                        CreditAccount.closed_at.is_(None),
                    )
                    .order_by(CreditAccount.membername, CreditAccount.opened_at)
                )
            )
            .scalars()
            .all()
        )
        moved: dict[str, int] = {}
        for account in accounts:
            balance = await lifecycle.balance_of(account.id)
            if balance <= 0:
                continue
            if account.valid_until <= now:
                # Expired credit is never destroyed (R61); it stays bound
                # for an admin to extend rather than being moved into a
                # window that has already closed.
                logger.info(
                    "credit sweep: account %s expired, left for an admin", account.code
                )
                continue
            _, created = await lifecycle.transfer(
                code=account.code,
                penalty=0,
                valid_from=account.valid_from,
                valid_until=account.valid_until,
                reason=f"Programme {event.id} ended; credit released",
                actor=None,
            )
            moved[account.membername] = moved.get(account.membername, 0) + (
                created.balance if created is not None else 0
            )
        for membername, credits in moved.items():
            await audit.log(
                actor_username=SYSTEM_ACTOR,
                action=AuditAction.CREDIT_RELEASED,
                target_username=membername,
                resource_type="event",
                resource_id=str(event.id),
                details={"credits": credits, "eventId": event.id},
            )
            _ = await notifier.notify_for_event(
                NotificationEvent(
                    type="credit.released",
                    recipients=[membername],
                    data={
                        "eventId": event.id,
                        "eventTitle": event.title,
                        "credits": credits,
                    },
                )
            )
        event.credit_released_at = now
        released += 1
    if released:
        await session.flush()
    return released
