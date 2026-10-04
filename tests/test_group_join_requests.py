"""Tests for the join-request workflow."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.group_join_request import (
    GroupJoinRequest,
    JoinRequestStatus,
)

from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)

DOB_2010 = 1262304000000
DOB_2014 = 1388534400000
ONE_DAY_MS = 86_400_000


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_manual(client: AsyncClient, admin_token: str, name: str = "M") -> int:
    g = await client.post("/v1/groups", json={"name": name}, headers=auth(admin_token))
    assert g.status_code == 201
    return g.json()["id"]


async def _make_semi_auto(
    client: AsyncClient,
    admin_token: str,
    *,
    name: str = "S",
    after: int = DOB_2010,
    before: int = DOB_2014,
) -> int:
    g = await client.post(
        "/v1/groups",
        json={
            "name": name,
            "dobOnOrAfterUtc": after,
            "dobOnOrBeforeUtc": before,
            "semiAuto": True,
        },
        headers=auth(admin_token),
    )
    assert g.status_code == 201
    return g.json()["id"]


@pytest.mark.requirement("groups:R51")
@pytest.mark.requirement("groups:R57")
@pytest.mark.requirement("groups:R58")
@pytest.mark.requirement("groups:R62")
@pytest.mark.asyncio
async def test_member_can_create_join_request_for_manual(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert create.status_code == 201
    rid = create.json()["id"]
    assert create.json()["status"] == "pending"

    user_view = await client.get(
        "/v1/mygroups/by_id/amy/requests", headers=auth(member_token)
    )
    assert user_view.status_code == 200
    assert any(r["id"] == rid and r["status"] == "pending" for r in user_view.json())

    admin_view = await client.get(
        f"/v1/groups/by_id/{gid}/requests?status=pending", headers=auth(admin_token)
    )
    assert admin_view.status_code == 200
    assert any(r["id"] == rid for r in admin_view.json())


@pytest.mark.requirement("groups:R52")
@pytest.mark.asyncio
async def test_member_can_create_join_request_for_eligible_semi_auto(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="ok", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    gid = await _make_semi_auto(client, admin_token)

    create = await client.post(
        f"/v1/mygroups/by_id/ok/join/{gid}", headers=auth(member_token)
    )
    assert create.status_code == 201
    assert create.json()["status"] == "pending"


@pytest.mark.requirement("groups:R53")
@pytest.mark.asyncio
async def test_member_blocked_from_request_for_ineligible_semi_auto(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="young", date_of_birth=DOB_2010 - ONE_DAY_MS
    )
    gid = await _make_semi_auto(client, admin_token)

    create = await client.post(
        f"/v1/mygroups/by_id/young/join/{gid}", headers=auth(member_token)
    )
    assert create.status_code == 422
    assert create.json()["detail"]["code"] == "NOT_ELIGIBLE"

    admin_view = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(admin_token)
    )
    assert admin_view.json() == []


@pytest.mark.requirement("groups:R54")
@pytest.mark.asyncio
async def test_member_blocked_from_request_for_auto(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    g = await client.post(
        "/v1/groups",
        json={"name": "A", "dobOnOrAfterUtc": DOB_2010},
        headers=auth(admin_token),
    )
    gid = g.json()["id"]

    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert create.status_code == 422
    assert create.json()["detail"]["code"] == "AUTO_GROUP_NOT_JOINABLE"


@pytest.mark.requirement("groups:R55")
@pytest.mark.asyncio
async def test_duplicate_pending_request_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    first = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert first.status_code == 201

    second = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "REQUEST_PENDING"


@pytest.mark.requirement("groups:R56")
@pytest.mark.asyncio
async def test_request_for_already_member_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )

    response = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ALREADY_MEMBER"


@pytest.mark.requirement("groups:R59")
@pytest.mark.requirement("groups:R62")
@pytest.mark.asyncio
async def test_user_can_cancel_their_pending_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    cancel = await client.delete(
        f"/v1/mygroups/by_id/amy/requests/{rid}", headers=auth(member_token)
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    admin_view = await client.get(
        f"/v1/groups/by_id/{gid}/requests?status=pending", headers=auth(admin_token)
    )
    assert all(r["id"] != rid for r in admin_view.json())


@pytest.mark.asyncio
async def test_user_cannot_cancel_others_request(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    a_token = await create_member_user(db_session, username="alice")
    b_token = await create_member_user(db_session, username="bob")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/alice/join/{gid}", headers=auth(a_token)
    )
    rid = create.json()["id"]

    response = await client.delete(
        f"/v1/mygroups/by_id/bob/requests/{rid}", headers=auth(b_token)
    )
    assert response.status_code in (403, 404)


@pytest.mark.requirement("groups:R63")
@pytest.mark.asyncio
async def test_admin_approve_adds_member_and_marks_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    assert approve.json()["decidedBy"] == "admin"
    assert approve.json()["decidedAt"] is not None

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=auth(admin_token)
    )
    assert "amy" in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R64")
@pytest.mark.asyncio
async def test_admin_reject_marks_rejected_no_member_added(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/reject",
        json={"reason": "not now"},
        headers=auth(admin_token),
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"
    assert reject.json()["reason"] == "not now"

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=auth(admin_token)
    )
    assert "amy" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R66")
@pytest.mark.asyncio
async def test_approval_re_checks_semi_auto_eligibility(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="ok", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    gid = await _make_semi_auto(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/ok/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    # Narrow window so 'ok' is no longer eligible.
    narrow = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": DOB_2014},
        headers=auth(admin_token),
    )
    assert narrow.status_code == 200

    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 422
    assert approve.json()["detail"]["code"] == "NOT_ELIGIBLE"

    members = await client.get(
        f"/v1/groups/by_id/{gid}/members", headers=auth(admin_token)
    )
    assert "ok" not in [m["membername"] for m in members.json()]


@pytest.mark.requirement("groups:R67")
@pytest.mark.asyncio
async def test_approval_succeeds_for_admin_target_even_if_outside_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    other_admin_token = await create_regular_admin_user(
        db_session, username="other_admin"
    )
    gid = await _make_semi_auto(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/other_admin/join/{gid}", headers=auth(other_admin_token)
    )
    assert create.status_code == 201
    rid = create.json()["id"]

    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"


@pytest.mark.requirement("groups:R45")
@pytest.mark.asyncio
async def test_direct_add_resolves_pending_request_as_approved(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    add = await client.post(
        f"/v1/groups/by_id/{gid}/members/byname/amy", headers=auth(admin_token)
    )
    assert add.status_code == 201

    requests = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(admin_token)
    )
    matched = next(r for r in requests.json() if r["id"] == rid)
    assert matched["status"] == "approved"
    assert matched["decidedBy"] == "admin"


@pytest.mark.requirement("groups:R45")
@pytest.mark.asyncio
async def test_bulk_add_resolves_pending_requests_for_added_only(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    ok_token = await create_member_user(
        db_session, username="ok", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    bad_token = await create_member_user(
        db_session, username="bad", date_of_birth=DOB_2010 - ONE_DAY_MS
    )
    gid = await _make_semi_auto(client, admin_token)

    rok = await client.post(f"/v1/mygroups/by_id/ok/join/{gid}", headers=auth(ok_token))
    assert rok.status_code == 201
    # 'bad' would be 422 NOT_ELIGIBLE — bypass the eligibility check by
    # creating a pending request directly via service: skip; instead
    # simulate by widening the criteria, requesting, then narrowing.
    widen = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": DOB_2010 - 5 * ONE_DAY_MS},
        headers=auth(admin_token),
    )
    assert widen.status_code == 200
    rbad = await client.post(
        f"/v1/mygroups/by_id/bad/join/{gid}", headers=auth(bad_token)
    )
    assert rbad.status_code == 201
    bad_rid = rbad.json()["id"]
    ok_rid = rok.json()["id"]
    # narrow back so 'bad' is ineligible again
    narrow = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": DOB_2010},
        headers=auth(admin_token),
    )
    assert narrow.status_code == 200

    bulk = await client.post(
        f"/v1/groups/by_id/{gid}/members/bulk",
        json={"membernames": ["ok", "bad"]},
        headers=auth(admin_token),
    )
    assert bulk.status_code == 200
    body = bulk.json()
    assert body["added"] == ["ok"]
    assert body["notEligible"] == ["bad"]

    requests = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(admin_token)
    )
    by_id = {r["id"]: r for r in requests.json()}
    assert by_id[ok_rid]["status"] == "approved"
    assert by_id[bad_rid]["status"] == "pending"


@pytest.mark.requirement("groups:R65")
@pytest.mark.asyncio
async def test_coach_cannot_approve_or_reject(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coachjoe")
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(coach_token)
    )
    assert approve.status_code == 403
    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/reject", headers=auth(coach_token)
    )
    assert reject.status_code == 403


@pytest.mark.requirement("groups:R68")
@pytest.mark.asyncio
async def test_request_endpoints_404_on_unknown_request_id(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    gid = await _make_manual(client, admin_token)

    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/9999/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 404
    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/9999/reject", headers=auth(admin_token)
    )
    assert reject.status_code == 404


@pytest.mark.requirement("groups:R80")
@pytest.mark.asyncio
async def test_audit_log_entries_written_for_create_cancel_approve_reject(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    # create + cancel
    c1 = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid1 = c1.json()["id"]
    await client.delete(
        f"/v1/mygroups/by_id/amy/requests/{rid1}", headers=auth(member_token)
    )

    # create + reject
    c2 = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid2 = c2.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid2}/reject",
        json={"reason": "x"},
        headers=auth(admin_token),
    )

    # create + approve
    c3 = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid3 = c3.json()["id"]
    await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid3}/approve", headers=auth(admin_token)
    )

    rows = (
        await db_session.execute(
            select(AuditLog.action).where(
                AuditLog.resource_type == "group_join_request"
            )
        )
    ).all()
    actions = {r[0] for r in rows}
    assert {
        "create_group_join_request",
        "cancel_group_join_request",
        "reject_group_join_request",
        "approve_group_join_request",
    }.issubset(actions)


# =============================================================================
# Gap-fill tests (R57, R61 cancelled, R62 coach, R66 pending, R69 non-pending)
# =============================================================================


@pytest.mark.requirement("groups:R57")
@pytest.mark.asyncio
async def test_reason_persisted_on_create(
    client: AsyncClient, db_session: AsyncSession
):
    """R57: optional reason on create is persisted and returned."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}",
        json={"reason": "want to play"},
        headers=auth(member_token),
    )
    assert create.status_code == 201
    assert create.json()["reason"] == "want to play"

    listing = await client.get(
        "/v1/mygroups/by_id/amy/requests", headers=auth(member_token)
    )
    matched = next(r for r in listing.json() if r["id"] == create.json()["id"])
    assert matched["reason"] == "want to play"


@pytest.mark.requirement("groups:R62")
@pytest.mark.asyncio
async def test_coach_can_list_group_requests(
    client: AsyncClient, db_session: AsyncSession
):
    """R62: coach can list a group's join requests."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, username="coach1")
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    listing = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(coach_token)
    )
    assert listing.status_code == 200
    assert any(r["id"] == rid for r in listing.json())


@pytest.mark.requirement("groups:R66")
@pytest.mark.asyncio
async def test_failed_re_check_leaves_request_pending(
    client: AsyncClient, db_session: AsyncSession
):
    """R66: when approval re-check fails, the request stays in pending state."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(
        db_session, username="ok", date_of_birth=DOB_2010 + ONE_DAY_MS
    )
    gid = await _make_semi_auto(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/ok/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    narrow = await client.patch(
        f"/v1/groups/by_id/{gid}",
        json={"dobOnOrAfterUtc": DOB_2014},
        headers=auth(admin_token),
    )
    assert narrow.status_code == 200

    approve = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(admin_token)
    )
    assert approve.status_code == 422

    listing = await client.get(
        f"/v1/groups/by_id/{gid}/requests", headers=auth(admin_token)
    )
    matched = next(r for r in listing.json() if r["id"] == rid)
    assert matched["status"] == "pending"
    assert matched["decidedAt"] is None
    assert matched["decidedBy"] is None


@pytest.mark.requirement("groups:R69")
@pytest.mark.asyncio
async def test_approve_on_already_approved_request_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    """R69: approve on a non-pending request returns 409."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    first = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(admin_token)
    )
    assert first.status_code == 200

    second = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/approve", headers=auth(admin_token)
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "JOIN_REQUEST_NOT_PENDING"


@pytest.mark.requirement("groups:R69")
@pytest.mark.asyncio
async def test_reject_on_cancelled_request_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    """R69: reject on a non-pending request returns 409."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)
    create = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = create.json()["id"]

    cancel = await client.delete(
        f"/v1/mygroups/by_id/amy/requests/{rid}", headers=auth(member_token)
    )
    assert cancel.status_code == 200

    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/reject",
        json={"reason": "n/a"},
        headers=auth(admin_token),
    )
    assert reject.status_code == 409
    assert reject.json()["detail"]["code"] == "JOIN_REQUEST_NOT_PENDING"


@pytest.mark.requirement("groups:R61")
@pytest.mark.asyncio
async def test_issue_145_request_after_cancel_reuses_same_row(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 145: re-requesting after self-cancel flips the same row
    back to pending instead of inserting a new one.
    """
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    first = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    cancel = await client.delete(
        f"/v1/mygroups/by_id/amy/requests/{first_id}", headers=auth(member_token)
    )
    assert cancel.status_code == 200

    second = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    assert second.status_code == 201
    assert second.json()["id"] == first_id, (
        "Re-request after cancel must reuse the existing row id"
    )
    assert second.json()["status"] == "pending"
    assert second.json()["decidedAt"] is None
    assert second.json()["decidedBy"] is None

    # listMyRequests returns at most one row per (user, group).
    listing = await client.get(
        "/v1/mygroups/by_id/amy/requests", headers=auth(member_token)
    )
    assert listing.status_code == 200
    rows_for_group = [r for r in listing.json() if r["groupId"] == gid]
    assert len(rows_for_group) == 1


@pytest.mark.requirement("groups:R61")
@pytest.mark.asyncio
async def test_issue_145_request_after_reject_reuses_same_row(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 145: re-requesting after admin reject also reuses the row.

    Audit trail of the prior rejection lives in audit_log; the
    lifecycle row is overwritten to reflect the new pending state.
    """
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    first = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
    )
    rid = first.json()["id"]

    reject = await client.post(
        f"/v1/groups/by_id/{gid}/requests/{rid}/reject",
        json={"reason": "try later"},
        headers=auth(admin_token),
    )
    assert reject.status_code == 200

    second = await client.post(
        f"/v1/mygroups/by_id/amy/join/{gid}",
        json={"reason": "trying again"},
        headers=auth(member_token),
    )
    assert second.status_code == 201
    assert second.json()["id"] == rid
    assert second.json()["status"] == "pending"
    assert second.json()["reason"] == "trying again"
    assert second.json()["decidedAt"] is None


@pytest.mark.requirement("groups:R61")
@pytest.mark.asyncio
async def test_issue_145_repeated_request_cancel_cycles_keep_single_row(
    client: AsyncClient, db_session: AsyncSession
):
    """Issue 145: three request/cancel cycles leave exactly one row.

    Pre-fix behavior: three rows accumulated (one per attempt). This
    test pins the new invariant — at most one row per (group, user).
    """
    admin_token = await create_admin_user(db_session)
    member_token = await create_member_user(db_session, username="amy")
    gid = await _make_manual(client, admin_token)

    first_id: int | None = None
    for _ in range(3):
        created = await client.post(
            f"/v1/mygroups/by_id/amy/join/{gid}", headers=auth(member_token)
        )
        assert created.status_code == 201
        if first_id is None:
            first_id = created.json()["id"]
        rid = created.json()["id"]
        assert rid == first_id

        cancel = await client.delete(
            f"/v1/mygroups/by_id/amy/requests/{rid}", headers=auth(member_token)
        )
        assert cancel.status_code == 200

    rows = await db_session.execute(
        select(GroupJoinRequest).where(
            GroupJoinRequest.group_id == gid,
            GroupJoinRequest.username == "amy",
        )
    )
    all_rows = list(rows.scalars())
    assert len(all_rows) == 1
    assert all_rows[0].id == first_id
    assert all_rows[0].status == JoinRequestStatus.cancelled.value
