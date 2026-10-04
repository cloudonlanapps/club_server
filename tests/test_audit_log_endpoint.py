"""Tests for GET /v1/audit_log.

Covers:
- Auth gate (super admin only; admin/coach/member → 403)
- Pagination (offset, limit, clamp, total)
- Ordering (timestamp desc, then id desc)
- Filters (actor, action, from_ts, to_ts)
- Resolution of users/events/groups/venues, including soft-deleted referents
- Occurrence composite resource_id split
- `details` enrichment for username/event/group/venue keys
- Forward compatibility: unknown resource_type returns label=null
- Missing referent yields null without raising
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.event import Event
from club_server.db.models.group import Group
from club_server.db.models.user import User, UserStatus
from club_server.db.models.venue import Venue
from club_server.services.auth import AuthService
from club_server.utils import now_utc_ms
from tests.helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)


ENDPOINT = "/v1/audit_log"


async def _insert_audit(
    db: AsyncSession,
    *,
    timestamp: int,
    action: str,
    actor_username: str | None = None,
    target_username: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        timestamp=timestamp,
        actor_username=actor_username,
        target_username=target_username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=json.dumps(details) if details is not None else None,
    )
    db.add(entry)
    await db.flush()
    return entry


async def _insert_user(
    db: AsyncSession,
    username: str,
    first: str | None = None,
    last: str | None = None,
    *,
    deleted: bool = False,
) -> User:
    user = User(
        username=username,
        password=AuthService.hash_password("x"),
        first_name=first,
        last_name=last,
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        created_at=now_utc_ms(),
        deleted_at=now_utc_ms() if deleted else None,
    )
    db.add(user)
    await db.flush()
    return user


async def _insert_venue(
    db: AsyncSession, venue_id: int, name: str, *, deleted: bool = False
) -> Venue:
    ts = now_utc_ms()
    v = Venue(
        id=venue_id,
        name=name,
        created_at=ts,
        updated_at=ts,
        deleted_at=ts if deleted else None,
    )
    db.add(v)
    await db.flush()
    return v


async def _insert_event(
    db: AsyncSession, event_id: int, title: str, *, deleted: bool = False
) -> Event:
    venue = await _insert_venue(db, 1000 + event_id, f"venue_for_{event_id}")
    ts = now_utc_ms()
    ev = Event(
        id=event_id,
        title=title,
        type="camp",
        visibility="public",
        venue_id=venue.id,
        start_time=ts,
        end_time=ts + 3600_000,
        created_at=ts,
        updated_at=ts,
        deleted_at=ts if deleted else None,
    )
    db.add(ev)
    await db.flush()
    return ev


async def _insert_group(
    db: AsyncSession, group_id: int, name: str, *, deleted: bool = False
) -> Group:
    ts = now_utc_ms()
    g = Group(
        id=group_id,
        name=name,
        kind="manual",
        created_at=ts,
        deleted_at=ts if deleted else None,
    )
    db.add(g)
    await db.flush()
    return g


# ---- Auth gate ----


@pytest.mark.requirement("platform:R17")
@pytest.mark.requirement("platform:R22")
@pytest.mark.asyncio
async def test_super_admin_can_read(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["rows"] == []
    assert body["offset"] == 0
    assert body["limit"] == 50


@pytest.mark.requirement("platform:R18")
@pytest.mark.asyncio
async def test_regular_admin_forbidden(client: AsyncClient, db_session: AsyncSession):
    token = await create_regular_admin_user(db_session)
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.requirement("platform:R18")
@pytest.mark.asyncio
async def test_coach_forbidden(client: AsyncClient, db_session: AsyncSession):
    token = await create_coach_user(db_session)
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.requirement("platform:R18")
@pytest.mark.asyncio
async def test_member_forbidden(client: AsyncClient, db_session: AsyncSession):
    token = await create_member_user(db_session)
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.requirement("platform:R18")
@pytest.mark.asyncio
async def test_unauthenticated_rejected(client: AsyncClient):
    resp = await client.get(ENDPOINT)
    assert resp.status_code in (401, 403)


# ---- Ordering ----


@pytest.mark.requirement("platform:R22")
@pytest.mark.asyncio
async def test_ordering_newest_first(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    a = await _insert_audit(db_session, timestamp=1000, action="a1")
    b = await _insert_audit(db_session, timestamp=3000, action="a2")
    c = await _insert_audit(db_session, timestamp=2000, action="a3")
    a_id, b_id, c_id = a.id, b.id, c.id

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert [r["id"] for r in rows] == [b_id, c_id, a_id]


@pytest.mark.requirement("platform:R22")
@pytest.mark.asyncio
async def test_ordering_tie_break_by_id_desc(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    a = await _insert_audit(db_session, timestamp=1000, action="a1")
    b = await _insert_audit(db_session, timestamp=1000, action="a2")
    c = await _insert_audit(db_session, timestamp=1000, action="a3")
    a_id, b_id, c_id = a.id, b.id, c.id

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    rows = resp.json()["rows"]
    assert [r["id"] for r in rows] == [c_id, b_id, a_id]


# ---- Pagination ----


@pytest.mark.requirement("platform:R22")
@pytest.mark.asyncio
async def test_pagination_offset_limit(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    ids: list[int] = []
    for i in range(5):
        e = await _insert_audit(db_session, timestamp=1000 + i, action=f"a{i}")
        ids.append(e.id)

    resp = await client.get(
        f"{ENDPOINT}?offset=1&limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.json()
    assert body["total"] == 5
    assert body["offset"] == 1
    assert body["limit"] == 2
    # newest-first: ids[4],[3],[2],[1],[0]; offset 1 limit 2 → [3],[2]
    assert [r["id"] for r in body["rows"]] == [ids[3], ids[2]]


@pytest.mark.requirement("platform:R22")
@pytest.mark.asyncio
async def test_pagination_limit_clamped_high(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    resp = await client.get(
        f"{ENDPOINT}?limit=5000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["limit"] == 200


@pytest.mark.requirement("platform:R22")
@pytest.mark.asyncio
async def test_pagination_limit_clamped_low(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    resp = await client.get(
        f"{ENDPOINT}?limit=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["limit"] == 1


# ---- Filters ----


@pytest.mark.requirement("platform:R23")
@pytest.mark.asyncio
async def test_filter_by_actor(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_audit(db_session, timestamp=1, action="x", actor_username="alice")
    await _insert_audit(db_session, timestamp=2, action="x", actor_username="bob")
    await _insert_audit(db_session, timestamp=3, action="x", actor_username="alice")

    resp = await client.get(
        f"{ENDPOINT}?actor=alice",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.json()
    assert body["total"] == 2
    assert all(r["actor"]["username"] == "alice" for r in body["rows"])


@pytest.mark.requirement("platform:R23")
@pytest.mark.asyncio
async def test_filter_by_action(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_audit(db_session, timestamp=1, action="create_event")
    await _insert_audit(db_session, timestamp=2, action="delete_event")
    await _insert_audit(db_session, timestamp=3, action="create_event")

    resp = await client.get(
        f"{ENDPOINT}?action=create_event",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.json()
    assert body["total"] == 2
    assert all(r["action"] == "create_event" for r in body["rows"])


@pytest.mark.requirement("platform:R23")
@pytest.mark.asyncio
async def test_filter_by_timestamp_range(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_audit(db_session, timestamp=100, action="x")
    mid = await _insert_audit(db_session, timestamp=200, action="x")
    await _insert_audit(db_session, timestamp=300, action="x")
    mid_id = mid.id

    resp = await client.get(
        f"{ENDPOINT}?from_ts=150&to_ts=250",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["id"] == mid_id


# ---- Resolution ----


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_actor_and_target_resolved(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_user(db_session, "sudo", "Sudo", "Admin")
    await _insert_user(db_session, "akilan", "Akilan", "Anand")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="some_action",
        actor_username="sudo",
        target_username="akilan",
    )

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["actor"] == {"username": "sudo", "fullName": "Sudo Admin"}
    assert row["target"] == {"username": "akilan", "fullName": "Akilan Anand"}


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_event_resource_resolved(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_event(db_session, 42, "Summer Camp")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="create_event",
        resource_type="event",
        resource_id="42",
    )

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"] == {"type": "event", "id": 42, "label": "Summer Camp"}


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_group_resource_resolved(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_group(db_session, 7, "Manual Group 1")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="create_group",
        resource_type="group",
        resource_id="7",
    )

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"] == {"type": "group", "id": 7, "label": "Manual Group 1"}


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_venue_resource_resolved(client: AsyncClient, db_session: AsyncSession):
    token = await create_admin_user(db_session)
    await _insert_venue(db_session, 5, "Rink One")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="create_venue",
        resource_type="venue",
        resource_id="5",
    )

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"] == {"type": "venue", "id": 5, "label": "Rink One"}


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_occurrence_composite_split(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_event(db_session, 380, "test_Leave")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="attendance_marked",
        resource_type="occurrence",
        resource_id="380:1825322400000",
    )

    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    res = resp.json()["rows"][0]["resource"]
    assert res["type"] == "occurrence"
    assert res["eventId"] == 380
    assert res["eventTitle"] == "test_Leave"
    assert res["occurrenceTimeUtc"] == 1825322400000


# ---- Soft-delete look-through ----


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_soft_deleted_user_still_resolves(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_user(db_session, "ghost", "Ghost", "User", deleted=True)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="login",
        actor_username="ghost",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["actor"] == {"username": "ghost", "fullName": "Ghost User"}


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_soft_deleted_event_still_resolves(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_event(db_session, 99, "Past Event", deleted=True)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="delete_event",
        resource_type="event",
        resource_id="99",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"]["label"] == "Past Event"


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_soft_deleted_group_still_resolves(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_group(db_session, 50, "Old Group", deleted=True)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="delete_group",
        resource_type="group",
        resource_id="50",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"]["label"] == "Old Group"


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_soft_deleted_venue_still_resolves(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_venue(db_session, 8, "Old Rink", deleted=True)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="delete_venue",
        resource_type="venue",
        resource_id="8",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"]["label"] == "Old Rink"


# ---- details enrichment ----


@pytest.mark.requirement("platform:R25")
@pytest.mark.asyncio
async def test_details_enrichment_username_keys(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_user(db_session, "alice", "Alice", "A")
    await _insert_user(db_session, "bob", "Bob", "B")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="x",
        details={"username": "alice", "target_username": "bob", "other": 1},
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    details = resp.json()["rows"][0]["details"]
    assert details["username"] == "alice"
    assert details["target_username"] == "bob"
    assert details["full_name"] == "Alice A"
    assert details["target_full_name"] == "Bob B"
    assert details["other"] == 1


@pytest.mark.requirement("platform:R25")
@pytest.mark.asyncio
async def test_details_enrichment_event_id(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_event(db_session, 12, "Camp X")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="x",
        details={"event_id": 12},
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    details = resp.json()["rows"][0]["details"]
    assert details["event_id"] == 12
    assert details["event_title"] == "Camp X"


@pytest.mark.requirement("platform:R25")
@pytest.mark.asyncio
async def test_details_enrichment_group_id(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_group(db_session, 1, "Manual Group 1")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="create_group_join_request",
        details={"group_id": 1},
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    details = resp.json()["rows"][0]["details"]
    assert details["group_id"] == 1
    assert details["group_name"] == "Manual Group 1"


@pytest.mark.requirement("platform:R25")
@pytest.mark.asyncio
async def test_details_enrichment_venue_id(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_venue(db_session, 3, "Main Rink")
    await _insert_audit(
        db_session,
        timestamp=1,
        action="x",
        details={"venue_id": 3},
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    details = resp.json()["rows"][0]["details"]
    assert details["venue_id"] == 3
    assert details["venue_name"] == "Main Rink"


@pytest.mark.requirement("platform:R25")
@pytest.mark.asyncio
async def test_details_unknown_keys_preserved(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="x",
        details={"foo": "bar", "count": 7, "nested": {"a": 1}},
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    details = resp.json()["rows"][0]["details"]
    assert details["foo"] == "bar"
    assert details["count"] == 7
    assert details["nested"] == {"a": 1}


# ---- Forward compat / missing referent ----


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_unknown_resource_type_passes_through(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="x",
        resource_type="future_thing",
        resource_id="999",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    res = resp.json()["rows"][0]["resource"]
    assert res["type"] == "future_thing"
    assert res["label"] is None


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_missing_user_referent_yields_null(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="login",
        actor_username="ghost_never_existed",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["actor"]["username"] == "ghost_never_existed"
    assert row["actor"]["fullName"] is None


@pytest.mark.requirement("platform:R24")
@pytest.mark.asyncio
async def test_missing_event_referent_yields_null_label(
    client: AsyncClient, db_session: AsyncSession
):
    token = await create_admin_user(db_session)
    await _insert_audit(
        db_session,
        timestamp=1,
        action="delete_event",
        resource_type="event",
        resource_id="9999",
    )
    resp = await client.get(ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    row = resp.json()["rows"][0]
    assert row["resource"] == {"type": "event", "id": 9999, "label": None}
