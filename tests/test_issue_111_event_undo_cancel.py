"""Tests for #111 — event undo-cancel endpoint + hybrid undo-notification policy.

The policy (event AND occurrence undo): for each recipient, if their
cancellation notification is unread and within the grace window, delete it and
fire nothing; otherwise keep it and fire a `*.restored` notification.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.notification import Notification
from club_server.services.notification import UNDO_GRACE_WINDOW_MS

from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import occurrence_version

DAY_MS = 86_400_000


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def future_floor_ms(hours: int) -> int:
    raw = int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp() * 1000)
    return (raw // 1000) * 1000


async def _notifs(
    db_session: AsyncSession, username: str, ntype: str
) -> list[Notification]:
    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.username == username,
            Notification.type == ntype,
        )
    )
    return list(result.scalars().all())


async def _mark_read(db_session: AsyncSession, username: str, ntype: str) -> None:
    rows = await _notifs(db_session, username, ntype)
    for row in rows:
        row.is_read = 1
    await db_session.flush()


async def _age_notifs(
    db_session: AsyncSession, username: str, ntype: str, ms: int
) -> None:
    rows = await _notifs(db_session, username, ntype)
    for row in rows:
        row.created_at = row.created_at - ms
    await db_session.flush()


async def _create_camp(
    client: AsyncClient,
    admin_token: str,
    *,
    organizer: str | None = None,
    coaches: list[str] | None = None,
) -> tuple[int, int]:
    """Create a daily COUNT=5 camp; return (event_id, start_ms)."""
    venue = await client.post(
        "/v1/venues", json={"name": "V"}, headers=auth(admin_token)
    )
    venue_id = venue.json()["id"]
    start = future_floor_ms(24)
    body: dict[str, Any] = {
        "title": "Camp",
        "type": "camp",
        "visibility": "public",
        "venueId": venue_id,
        "startTimeUtc": start,
        "endTimeUtc": start + 3600_000,
        "rrule": "FREQ=DAILY;COUNT=5",
    }
    if organizer is not None:
        body["organizerName"] = organizer
    if coaches is not None:
        body["coachNames"] = coaches
    resp = await client.post("/v1/events", json=body, headers=auth(admin_token))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], start


async def _cancel(client: AsyncClient, token: str, event_id: int, effective: int):
    return await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "Force majeure", "effectiveDateTimeUtc": effective},
        headers=auth(token),
    )


# ---------------------------------------------------------------------------
# endpoint mechanics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_cancel_clears_until_time(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, start = await _create_camp(client, admin)
    assert (
        await _cancel(client, admin, event_id, start + 2 * DAY_MS)
    ).status_code == 200

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(admin)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["untilTimeUtc"] is None


@pytest.mark.asyncio
async def test_undo_on_non_cancelled_event_returns_400(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    event_id, _ = await _create_camp(client, admin)

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(admin)
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "EVENT_NOT_CANCELLED"


@pytest.mark.asyncio
async def test_undo_forbidden_for_unrelated_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mallory")
    event_id, start = await _create_camp(client, admin)
    assert (
        await _cancel(client, admin, event_id, start + 2 * DAY_MS)
    ).status_code == 200

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(member)
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# hybrid notification policy — event
# ---------------------------------------------------------------------------


async def _enroll(client: AsyncClient, admin: str, event_id: int, member: str) -> None:
    resp = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": [member]},
        headers=auth(admin),
    )
    assert resp.status_code in (200, 201, 204), resp.text


@pytest.mark.requirement("notifications:R47")
@pytest.mark.asyncio
async def test_undo_within_grace_unread_deletes_cancellation_no_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    event_id, start = await _create_camp(client, admin)
    await _enroll(client, admin, event_id, "amy")

    assert (
        await _cancel(client, admin, event_id, start + 2 * DAY_MS)
    ).status_code == 200
    assert len(await _notifs(db_session, "amy", "event.cancelled")) == 1

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(admin)
    )
    assert resp.status_code == 200
    # Unread + in-window → cancellation deleted, no restored.
    assert await _notifs(db_session, "amy", "event.cancelled") == []
    assert await _notifs(db_session, "amy", "event.restored") == []


@pytest.mark.requirement("notifications:R48")
@pytest.mark.asyncio
async def test_undo_after_read_preserves_cancellation_and_fires_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    event_id, start = await _create_camp(client, admin)
    await _enroll(client, admin, event_id, "amy")

    assert (
        await _cancel(client, admin, event_id, start + 2 * DAY_MS)
    ).status_code == 200
    await _mark_read(db_session, "amy", "event.cancelled")

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(admin)
    )
    assert resp.status_code == 200
    assert len(await _notifs(db_session, "amy", "event.cancelled")) == 1
    restored = await _notifs(db_session, "amy", "event.restored")
    assert len(restored) == 1
    assert restored[0].payload["data"]["eventId"] == event_id


@pytest.mark.requirement("notifications:R48")
@pytest.mark.asyncio
async def test_undo_past_grace_window_fires_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    event_id, start = await _create_camp(client, admin)
    await _enroll(client, admin, event_id, "amy")

    assert (
        await _cancel(client, admin, event_id, start + 2 * DAY_MS)
    ).status_code == 200
    await _age_notifs(
        db_session, "amy", "event.cancelled", UNDO_GRACE_WINDOW_MS + 60_000
    )

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(admin)
    )
    assert resp.status_code == 200
    assert len(await _notifs(db_session, "amy", "event.cancelled")) == 1
    assert len(await _notifs(db_session, "amy", "event.restored")) == 1


@pytest.mark.requirement("notifications:R49")
@pytest.mark.asyncio
async def test_undo_audience_includes_coach_and_organizer(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    await create_member_user(db_session, "olive")  # organizer
    await create_coach_user(db_session, "carl")
    event_id, start = await _create_camp(
        client, admin, organizer="olive", coaches=["carl"]
    )
    await _enroll(client, admin, event_id, "amy")

    assert (
        await _cancel(client, admin, event_id, start + 2 * DAY_MS)
    ).status_code == 200
    # Read all cancellations so undo fires restored to everyone.
    for u in ("amy", "olive", "carl"):
        await _mark_read(db_session, u, "event.cancelled")

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/undo-cancel", headers=auth(admin)
    )
    assert resp.status_code == 200
    for u in ("amy", "olive", "carl"):
        assert len(await _notifs(db_session, u, "event.restored")) == 1, (
            f"{u} missing restored"
        )


# ---------------------------------------------------------------------------
# hybrid notification policy — occurrence (retrofit)
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R47")
@pytest.mark.asyncio
async def test_occurrence_undo_within_grace_unread_deletes_cancellation(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    event_id, start = await _create_camp(client, admin)
    await _enroll(client, admin, event_id, "amy")
    occ = start + 2 * DAY_MS

    assert (
        await client.post(
            f"/v1/events/by_id/{event_id}/occurrences/{occ}/cancel",
            json={
                "version": await occurrence_version(client, admin, event_id, occ),
                "reason": "Weather",
            },
            headers=auth(admin),
        )
    ).status_code == 204
    assert len(await _notifs(db_session, "amy", "occurrence.cancelled")) == 1

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/undo-cancel",
        json={"version": await occurrence_version(client, admin, event_id, occ)},
        headers=auth(admin),
    )
    assert resp.status_code == 204
    assert await _notifs(db_session, "amy", "occurrence.cancelled") == []
    assert await _notifs(db_session, "amy", "occurrence.restored") == []


@pytest.mark.requirement("notifications:R48")
@pytest.mark.asyncio
async def test_occurrence_undo_after_read_fires_restored(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await create_member_user(db_session, "amy")
    event_id, start = await _create_camp(client, admin)
    await _enroll(client, admin, event_id, "amy")
    occ = start + 2 * DAY_MS

    assert (
        await client.post(
            f"/v1/events/by_id/{event_id}/occurrences/{occ}/cancel",
            json={
                "version": await occurrence_version(client, admin, event_id, occ),
                "reason": "Weather",
            },
            headers=auth(admin),
        )
    ).status_code == 204
    await _mark_read(db_session, "amy", "occurrence.cancelled")

    resp = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occ}/undo-cancel",
        json={"version": await occurrence_version(client, admin, event_id, occ)},
        headers=auth(admin),
    )
    assert resp.status_code == 204
    assert len(await _notifs(db_session, "amy", "occurrence.cancelled")) == 1
    assert len(await _notifs(db_session, "amy", "occurrence.restored")) == 1
