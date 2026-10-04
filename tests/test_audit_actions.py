"""Tests for #253 (Part 1) — the centralised ``AuditAction`` vocabulary.

Every value written to ``AuditLog.action`` must come from the ``AuditAction``
enum. These tests pin the enum's integrity, the dynamic media-link resolution
path, that ``AuditService.log`` persists the enum's wire string unchanged, and
guard against new raw ``action="..."`` string literals creeping back into the
routers (which would bypass the catalogue the summary registry is built on).
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.services.audit import AuditService
from club_server.services.audit_actions import AuditAction

_ROUTERS_DIR = Path(__file__).resolve().parents[1] / "club_server" / "routers"
# Matches a literal string/f-string passed to ``action=`` (the pattern we want
# to be gone). ``action=AuditAction(...)`` and ``action=some_var`` do not match.
_RAW_ACTION_LITERAL = re.compile(r'action\s*=\s*f?"')


@pytest.mark.requirement("platform:R15")
def test_audit_action_values_are_unique():
    values = [a.value for a in AuditAction]
    assert len(values) == len(set(values))


@pytest.mark.requirement("platform:R15")
@pytest.mark.parametrize("owner_type", ["user", "event", "group", "venue"])
def test_media_link_actions_resolvable(owner_type: str):
    """The media-link routers build the action dynamically from ``owner_type``;
    every (verb, owner_type) combination must map to a real enum member."""
    for verb in ("create", "update", "delete"):
        assert AuditAction(f"{verb}_{owner_type}_media_link")
    assert AuditAction(f"delete_{owner_type}_media_tag")


@pytest.mark.requirement("platform:R15")
def test_no_raw_action_literals_in_routers():
    """All audit calls must pass an ``AuditAction`` member, not a raw string."""
    offenders: list[str] = []
    for path in _ROUTERS_DIR.glob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _RAW_ACTION_LITERAL.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "raw action string literals found:\n" + "\n".join(offenders)


@pytest.mark.requirement("platform:R15")
@pytest.mark.asyncio
async def test_log_persists_action_wire_value(db_session: AsyncSession):
    """``AuditService.log`` stores the enum's string value in ``action``."""
    await AuditService(db_session).log(
        actor_username="tester",
        action=AuditAction.CANCEL_EVENT,
        resource_type="event",
        resource_id="42",
    )
    entry = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.actor_username == "tester")
        )
    ).scalar_one()
    assert entry.action == "cancel_event"
