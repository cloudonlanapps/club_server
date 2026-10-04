"""Tests for #57 — notification retention.

Covers:
  * ``ceil_to_utc_day`` utility (idempotent on midnight, ceils otherwise).
  * Admin system_preferences endpoints — super-admin RW; regular admin 403.
  * Per-broadcast ``expires_at_utc`` is ceiled to next midnight UTC on write.
  * Daily ``sweep_expired_notifications`` honours per-broadcast expiry,
    falls back to global retention, skips actionable-unresolved rows.
  * Default retention value used when the key is absent from the table.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.broadcast import Broadcast, BroadcastStatus
from club_server.db.models.notification import Notification
from club_server.db.models.system_preference import SystemPreference
from club_server.services.scheduler import sweep_expired_notifications
from club_server.services.system_preferences import SystemPreferenceService
from club_server.utils import MS_PER_DAY, ceil_to_utc_day, now_utc_ms

from .helpers import (
    create_admin_user,
    create_regular_admin_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# ceil_to_utc_day utility
# ---------------------------------------------------------------------------


def test_ceil_to_utc_day_idempotent_on_midnight():
    midnight = 1_700_000_000_000 - (1_700_000_000_000 % MS_PER_DAY)
    assert ceil_to_utc_day(midnight) == midnight


def test_ceil_to_utc_day_rounds_up_non_midnight():
    midnight = (1_700_000_000_000 // MS_PER_DAY) * MS_PER_DAY
    just_after = midnight + 1
    just_before_next = midnight + MS_PER_DAY - 1
    assert ceil_to_utc_day(just_after) == midnight + MS_PER_DAY
    assert ceil_to_utc_day(just_before_next) == midnight + MS_PER_DAY


def test_ceil_to_utc_day_epoch_zero():
    assert ceil_to_utc_day(0) == 0


# ---------------------------------------------------------------------------
# Admin preferences endpoints
# ---------------------------------------------------------------------------


@pytest.mark.requirement("platform:R1")
@pytest.mark.asyncio
async def test_admin_can_set_and_get_preference(
    client: AsyncClient, db_session: AsyncSession
):
    super_token = await create_admin_user(db_session)

    patch = await client.patch(
        "/v1/admin/preferences/notification_info_retention_days",
        json={"value": 30},
        headers=auth(super_token),
    )
    assert patch.status_code == 200
    assert patch.json()["value"] == 30
    assert patch.json()["updatedBy"] == "admin"

    get = await client.get(
        "/v1/admin/preferences/notification_info_retention_days",
        headers=auth(super_token),
    )
    assert get.status_code == 200
    assert get.json()["value"] == 30


@pytest.mark.requirement("platform:R2")
@pytest.mark.asyncio
async def test_list_preferences_returns_all_keys(
    client: AsyncClient, db_session: AsyncSession
):
    super_token = await create_admin_user(db_session)
    _ = await client.patch(
        "/v1/admin/preferences/notification_info_retention_days",
        json={"value": 45},
        headers=auth(super_token),
    )
    _ = await client.patch(
        "/v1/admin/preferences/some_other_key",
        json={"value": "hello"},
        headers=auth(super_token),
    )

    resp = await client.get("/v1/admin/preferences", headers=auth(super_token))
    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()["items"]}
    assert {"notification_info_retention_days", "some_other_key"} <= keys


@pytest.mark.requirement("platform:R3")
@pytest.mark.asyncio
async def test_get_preference_returns_empty_value_for_unknown_key_without_default(
    client: AsyncClient, db_session: AsyncSession
):
    super_token = await create_admin_user(db_session)
    resp = await client.get(
        "/v1/admin/preferences/nonexistent_key", headers=auth(super_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "key": "nonexistent_key",
        "value": None,
        "updatedAtUtc": None,
        "updatedBy": None,
    }


@pytest.mark.requirement("platform:R5")
@pytest.mark.asyncio
async def test_regular_admin_forbidden_from_listing_preferences(
    client: AsyncClient, db_session: AsyncSession
):
    regular_token = await create_regular_admin_user(db_session)
    list_resp = await client.get("/v1/admin/preferences", headers=auth(regular_token))
    assert list_resp.status_code == 403


@pytest.mark.requirement("platform:R5")
@pytest.mark.asyncio
async def test_regular_admin_forbidden_from_patching_preferences(
    client: AsyncClient, db_session: AsyncSession
):
    regular_token = await create_regular_admin_user(db_session)
    patch_resp = await client.patch(
        "/v1/admin/preferences/notification_info_retention_days",
        json={"value": 10},
        headers=auth(regular_token),
    )
    assert patch_resp.status_code == 403


@pytest.mark.requirement("platform:R4")
@pytest.mark.asyncio
async def test_default_value_used_when_key_absent(db_session: AsyncSession):
    service = SystemPreferenceService(db_session)
    value = await service.get_value("notification_info_retention_days")
    assert value == 90


# ---------------------------------------------------------------------------
# Broadcast ceil normalization
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R44")
@pytest.mark.asyncio
async def test_broadcast_expires_at_ceiled_to_next_midnight(
    client: AsyncClient, db_session: AsyncSession
):
    super_token = await create_admin_user(db_session)
    midnight = (now_utc_ms() // MS_PER_DAY) * MS_PER_DAY
    non_midnight = midnight + (5 * 60 * 60 * 1000)  # +5 hours

    resp = await client.post(
        "/v1/broadcasts",
        headers=auth(super_token),
        json={
            "audienceSelector": {"kind": "all_users"},
            "payload": {"v": 1, "type": "broadcast.message", "data": {"text": "hi"}},
            "expiresAtUtc": non_midnight,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expiresAtUtc"] == midnight + MS_PER_DAY


@pytest.mark.requirement("notifications:R44")
@pytest.mark.asyncio
async def test_broadcast_expires_at_already_midnight_unchanged(
    client: AsyncClient, db_session: AsyncSession
):
    super_token = await create_admin_user(db_session)
    midnight = ((now_utc_ms() // MS_PER_DAY) + 3) * MS_PER_DAY

    resp = await client.post(
        "/v1/broadcasts",
        headers=auth(super_token),
        json={
            "audienceSelector": {"kind": "all_users"},
            "payload": {"v": 1, "type": "broadcast.message", "data": {"text": "hi"}},
            "expiresAtUtc": midnight,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["expiresAtUtc"] == midnight


# ---------------------------------------------------------------------------
# Sweep behaviour
# ---------------------------------------------------------------------------


async def _make_target_user(db_session: AsyncSession, username: str = "alice") -> None:
    """Insert a minimal active user so notifications can FK to it."""
    import json as _json

    from club_server.db.models.user import User, UserStatus
    from club_server.services.auth import AuthService

    user = User(
        username=username,
        password=AuthService.hash_password("pw"),
        first_name="A",
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=_json.dumps({"roles": []}),
        created_at=now_utc_ms(),
    )
    db_session.add(user)
    await db_session.flush()


def _make_notification(
    *,
    username: str,
    created_at: int,
    broadcast_id: int | None = None,
    pending_action_type: str | None = None,
) -> Notification:
    return Notification(
        username=username,
        type="broadcast.message" if broadcast_id else "info.note",
        channel="app",
        payload={"v": 1, "type": "x", "data": {}},
        broadcast_id=broadcast_id,
        pending_action_type=pending_action_type,
        pending_action_id=None,
        pending_action_key=None,
        is_read=0,
        created_at=created_at,
    )


@pytest.mark.requirement("notifications:R42")
@pytest.mark.asyncio
async def test_sweep_deletes_non_broadcast_rows_past_global_retention(
    db_session: AsyncSession,
):
    await _make_target_user(db_session)
    now = now_utc_ms()
    midnight_now = (now // MS_PER_DAY) * MS_PER_DAY

    # Default retention is 90 days.
    expired_created = midnight_now - 91 * MS_PER_DAY
    fresh_created = midnight_now - 10 * MS_PER_DAY

    db_session.add(_make_notification(username="alice", created_at=expired_created))
    db_session.add(_make_notification(username="alice", created_at=fresh_created))
    await db_session.flush()

    deleted = await sweep_expired_notifications(db_session, now)
    assert deleted == 1

    remaining = (await db_session.execute(select(Notification))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].created_at == fresh_created


@pytest.mark.requirement("notifications:R43")
@pytest.mark.asyncio
async def test_sweep_honours_per_broadcast_expires_at(db_session: AsyncSession):
    await _make_target_user(db_session)
    now = now_utc_ms()
    midnight_now = (now // MS_PER_DAY) * MS_PER_DAY

    # Two broadcasts: one expired yesterday, one expires tomorrow.
    b_expired = Broadcast(
        sender_username=None,
        audience_selector={"kind": "all_users"},
        payload={},
        sent_at=midnight_now - 5 * MS_PER_DAY,
        expires_at=midnight_now - MS_PER_DAY,
        status=BroadcastStatus.sent.value,
    )
    b_future = Broadcast(
        sender_username=None,
        audience_selector={"kind": "all_users"},
        payload={},
        sent_at=midnight_now - 5 * MS_PER_DAY,
        expires_at=midnight_now + MS_PER_DAY,
        status=BroadcastStatus.sent.value,
    )
    db_session.add_all([b_expired, b_future])
    await db_session.flush()

    db_session.add(
        _make_notification(
            username="alice",
            created_at=midnight_now - 5 * MS_PER_DAY,
            broadcast_id=b_expired.id,
        )
    )
    db_session.add(
        _make_notification(
            username="alice",
            created_at=midnight_now - 5 * MS_PER_DAY,
            broadcast_id=b_future.id,
        )
    )
    await db_session.flush()

    deleted = await sweep_expired_notifications(db_session, now)
    assert deleted == 1

    remaining = (await db_session.execute(select(Notification))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].broadcast_id == b_future.id


@pytest.mark.requirement("notifications:R43")
@pytest.mark.asyncio
async def test_sweep_falls_back_to_global_when_broadcast_expires_null(
    db_session: AsyncSession,
):
    await _make_target_user(db_session)
    now = now_utc_ms()
    midnight_now = (now // MS_PER_DAY) * MS_PER_DAY

    b = Broadcast(
        sender_username=None,
        audience_selector={"kind": "all_users"},
        payload={},
        sent_at=midnight_now - 91 * MS_PER_DAY,
        expires_at=None,
        status=BroadcastStatus.sent.value,
    )
    db_session.add(b)
    await db_session.flush()

    db_session.add(
        _make_notification(
            username="alice",
            created_at=midnight_now - 91 * MS_PER_DAY,
            broadcast_id=b.id,
        )
    )
    await db_session.flush()

    deleted = await sweep_expired_notifications(db_session, now)
    assert deleted == 1


@pytest.mark.requirement("notifications:R45")
@pytest.mark.asyncio
async def test_sweep_skips_actionable_unresolved_notifications(
    db_session: AsyncSession,
):
    await _make_target_user(db_session)
    now = now_utc_ms()
    midnight_now = (now // MS_PER_DAY) * MS_PER_DAY

    db_session.add(
        _make_notification(
            username="alice",
            created_at=midnight_now - 365 * MS_PER_DAY,
            pending_action_type="enrollment_invite",
        )
    )
    await db_session.flush()

    deleted = await sweep_expired_notifications(db_session, now)
    assert deleted == 0

    remaining = (await db_session.execute(select(Notification))).scalars().all()
    assert len(remaining) == 1


@pytest.mark.requirement("notifications:R42")
@pytest.mark.asyncio
async def test_sweep_respects_overridden_retention_pref(db_session: AsyncSession):
    await _make_target_user(db_session)
    now = now_utc_ms()
    midnight_now = (now // MS_PER_DAY) * MS_PER_DAY

    # Shrink retention to 5 days.
    db_session.add(
        SystemPreference(
            key="notification_info_retention_days",
            value=5,
            updated_at=now,
            updated_by=None,
        )
    )
    await db_session.flush()

    db_session.add(
        _make_notification(username="alice", created_at=midnight_now - 6 * MS_PER_DAY)
    )
    db_session.add(
        _make_notification(username="alice", created_at=midnight_now - 3 * MS_PER_DAY)
    )
    await db_session.flush()

    deleted = await sweep_expired_notifications(db_session, now)
    assert deleted == 1
