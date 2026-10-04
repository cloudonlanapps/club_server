"""Tests for #253 (Part 3) — server-side human-readable audit summaries.

Pins the mandatory English-coverage contract, that every action renders
cleanly, representative wording (reason/role/IP/occurrence), the unknown-action
generic fallback, the skip-missing-language rule, and that the endpoint surfaces
a `summary` map on each row.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.services.audit_actions import AuditAction
from club_server.services.audit_summary import (
    SummaryContext,
    TEMPLATES,
    render_summaries,
)
from tests.helpers import create_admin_user


def _ctx(**kwargs) -> SummaryContext:
    base = dict(
        actor="Asha Patil",
        target="Bob Builder",
        resource_label="U10 Practice",
        resource_type="event",
        details={},
        timestamp_ms=1_748_000_000_000,
    )
    base.update(kwargs)
    return SummaryContext(**base)


# --- Contract ----------------------------------------------------------------


@pytest.mark.requirement("platform:R26")
def test_english_covers_every_action():
    """Mandatory: every AuditAction has an `en` template (no silent gaps)."""
    assert set(AuditAction) == set(TEMPLATES["en"])


@pytest.mark.requirement("platform:R26")
def test_every_action_renders_cleanly():
    """Each builder must produce a non-empty `en` sentence without raising —
    catches bad format placeholders or missing-detail crashes."""
    ctx = _ctx(
        details={
            "reason": "blurry id",
            "role": "coach",
            "status": "present",
            "email": "x@example.com",
            "group_name": "Squirts",
            "ownerType": "event",
            "tag": "cover",
            "added": ["a", "b"],
            "recipientCount": 5,
        }
    )
    for action in AuditAction:
        out = render_summaries(action.value, ctx)
        assert out["en"], action
        assert out["en"].endswith("."), action
        assert out["en"].startswith("Asha Patil "), action


# --- Wording -----------------------------------------------------------------


@pytest.mark.requirement("platform:R26")
def test_cancel_event_includes_reason_and_timestamp():
    out = render_summaries(
        AuditAction.CANCEL_EVENT.value, _ctx(details={"reason": "rink closed"})
    )
    assert out["en"] == (
        "Asha Patil cancelled event U10 Practice (reason: rink closed) "
        "on 2025-05-23 11:33 UTC."
    )


@pytest.mark.requirement("platform:R26")
def test_login_includes_ip_clause():
    out = render_summaries(
        AuditAction.LOGIN.value, _ctx(details={"ip_address": "1.2.3.4"})
    )
    assert out["en"].startswith("Asha Patil logged in on ")
    assert out["en"].endswith("from 1.2.3.4.")


@pytest.mark.requirement("platform:R26")
def test_assign_role_uses_role_detail():
    out = render_summaries(
        AuditAction.ASSIGN_ROLE.value, _ctx(details={"role": "coach"})
    )
    assert "assigned the coach role to Bob Builder" in out["en"]


@pytest.mark.requirement("platform:R26")
def test_occurrence_uses_event_title_label():
    out = render_summaries(
        AuditAction.CANCEL_OCCURRENCE.value,
        _ctx(resource_type="occurrence", details={"reason": "snow"}),
    )
    assert "an occurrence of event U10 Practice" in out["en"]
    assert "(reason: snow)" in out["en"]


# --- Fallback & language rules ----------------------------------------------


@pytest.mark.requirement("platform:R26")
def test_unknown_action_uses_generic_fallback():
    out = render_summaries("frobnicate_widget", _ctx())
    assert out["en"] == (
        "Asha Patil performed action 'frobnicate_widget' on 2025-05-23 11:33 UTC."
    )


@pytest.mark.requirement("platform:R26")
def test_missing_non_default_language_is_skipped():
    out = render_summaries(AuditAction.LOGIN.value, _ctx(), languages=("en", "fr"))
    assert "en" in out
    assert "fr" not in out  # no fr template → omitted, not a generic string


# --- Endpoint integration ----------------------------------------------------


@pytest.mark.requirement("platform:R26")
@pytest.mark.asyncio
async def test_endpoint_returns_summary(client: AsyncClient, db_session: AsyncSession):
    db_session.add(
        AuditLog(
            timestamp=1_748_000_000_000,
            actor_username="bob",
            action="login",
            details=json.dumps({"ip_address": "9.9.9.9"}),
        )
    )
    await db_session.flush()
    token = await create_admin_user(db_session)

    resp = await client.get(
        "/v1/audit_log", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["rows"] if r["action"] == "login")
    assert row["summary"]["en"].startswith("bob logged in on ")
    assert row["summary"]["en"].endswith("from 9.9.9.9.")
