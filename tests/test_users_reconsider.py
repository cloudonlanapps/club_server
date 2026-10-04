"""Tests for #122 — admin reconsider / self reapply workflow backed by
the new ``user_review_requests`` table.

Covers acceptance criteria from the issue:

* ``POST /v1/users/by_id/{u}/reconsider`` — 201, status flip
  ``pending → registered``, prior active row supersede, deletion of
  any ``user_approval`` notifications targeting the user, and audit
  log entry.
* ``POST .../reconsider`` — 404 on missing/deleted, 409 on non-pending,
  400 on self, 422 on reason out of bounds.
* ``PATCH .../reapply`` — 403 for non-self, 409 when no active row or
  caller status ≠ registered, atomic field update + row close with
  ``resolved`` resolution, audit log entry, caller status remains
  ``registered``.
* ``approve_user`` and ``block_user`` accept optional
  ``resolutionReason`` and close any active row with the corresponding
  resolution.
* Partial unique index — at most one active row per user.
* Round trip: reconsider → reapply → submit-for-review yields exactly
  one ``user_approval`` notification (not two).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.audit_log import AuditLog
from club_server.db.models.notification import Notification
from club_server.db.models.user import User, UserStatus
from club_server.db.models.user_review_request import UserReviewRequest

from .helpers import (
    attach_identity_document,
    create_admin_user,
    create_member_user,
    create_registered_user,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_pending_user(
    db_session: AsyncSession, client: AsyncClient, username: str = "rosa"
) -> str:
    """Create a user and walk them to ``status=pending`` via
    ``submit-for-review``, returning their access token."""
    token = await create_registered_user(db_session, username)
    await attach_identity_document(db_session, username)
    response = await client.post("/v1/users/me/submit-for-review", headers=auth(token))
    assert response.status_code == 200, response.text
    return token


# ---------------------------------------------------------------------------
# reconsider — happy path & side effects
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R15")
@pytest.mark.asyncio
async def test_reconsider_creates_active_row_and_returns_201(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "Please re-upload your ID, the image was blurry."},
    )
    assert response.status_code == 201, response.text

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(UserReviewRequest).where(UserReviewRequest.username == "rosa")
            )
        )
        .scalars()
        .all()
    )
    rows = list(rows)
    assert len(rows) == 1
    assert rows[0].resolved_at is None
    assert rows[0].requested_by == "admin"
    assert "blurry" in rows[0].reason


@pytest.mark.requirement("users:R15")
@pytest.mark.asyncio
async def test_reconsider_flips_status_pending_to_registered(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "needs more info"},
    )
    assert response.status_code == 201

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.status == UserStatus.registered.value


@pytest.mark.requirement("notifications:R38")
@pytest.mark.asyncio
async def test_reconsider_deletes_existing_user_approval_notifications(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    pre = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.pending_action_type == "user_approval",
                    Notification.pending_action_key == "rosa",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(pre)) == 1, "submit-for-review should have created exactly one"

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "needs more info"},
    )
    assert response.status_code == 201

    db_session.expire_all()
    post = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.pending_action_type == "user_approval",
                    Notification.pending_action_key == "rosa",
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(post) == []


@pytest.mark.requirement("users:R61")
@pytest.mark.asyncio
async def test_reconsider_writes_audit_log(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "needs more info"},
    )
    assert response.status_code == 201

    db_session.expire_all()
    entries = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "reconsider_user",
                    AuditLog.target_username == "rosa",
                    AuditLog.actor_username == "admin",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(entries)) == 1


@pytest.mark.requirement("users:R16")
@pytest.mark.asyncio
async def test_reconsider_supersedes_prior_active_row(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    # First reconsider (rosa → registered)
    r1 = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "first request"},
    )
    assert r1.status_code == 201

    # Rosa resubmits and is back to pending
    rosa_token = await client.post(
        "/v1/auth/login",
        json={"username": "rosa", "password": "regpass123"},
    )
    assert rosa_token.status_code == 200
    rosa_token_str: str = rosa_token.json()["accessToken"]
    reapply = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(rosa_token_str),
        json={"firstName": "Rosa2"},
    )
    assert reapply.status_code == 200, reapply.text
    submit = await client.post(
        "/v1/users/me/submit-for-review", headers=auth(rosa_token_str)
    )
    assert submit.status_code == 200

    # Second reconsider should supersede the (closed) row and create a new active one
    r2 = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "second request"},
    )
    assert r2.status_code == 201

    db_session.expire_all()
    all_rows = (
        (
            await db_session.execute(
                select(UserReviewRequest)
                .where(UserReviewRequest.username == "rosa")
                .order_by(UserReviewRequest.id.asc())
            )
        )
        .scalars()
        .all()
    )
    all_rows = list(all_rows)
    assert len(all_rows) == 2
    # First row was closed by reapply as 'resubmitted'
    assert all_rows[0].resolution == "resubmitted"
    assert all_rows[0].resolved_at is not None
    # Second row is currently active
    assert all_rows[1].resolution is None
    assert all_rows[1].resolved_at is None


@pytest.mark.requirement("users:R16")
@pytest.mark.asyncio
async def test_reconsider_supersedes_when_prior_row_still_active(
    client: AsyncClient, db_session: AsyncSession
):
    """If the prior row is somehow still active (e.g. admin reconsiders
    twice in a row without the user reapplying), the second call must
    close the first with ``superseded`` and insert a fresh active row."""
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    # First reconsider — user is now registered; row 1 is active
    r1 = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "first"},
    )
    assert r1.status_code == 201

    # Manually push them back to pending without going through reapply
    rosa = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    rosa.status = UserStatus.pending.value
    await db_session.commit()

    r2 = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "second"},
    )
    assert r2.status_code == 201

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(UserReviewRequest)
                .where(UserReviewRequest.username == "rosa")
                .order_by(UserReviewRequest.id.asc())
            )
        )
        .scalars()
        .all()
    )
    rows = list(rows)
    assert len(rows) == 2
    assert rows[0].resolution == "superseded"
    assert rows[0].resolved_by == "admin"
    assert rows[1].resolved_at is None


# ---------------------------------------------------------------------------
# reconsider — error paths
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R17")
@pytest.mark.asyncio
async def test_reconsider_404_when_user_missing(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/users/by_id/ghost/reconsider",
        headers=auth(admin_token),
        json={"reason": "x"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"


@pytest.mark.requirement("users:R17")
@pytest.mark.asyncio
async def test_reconsider_409_when_user_not_pending(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "alice")  # status=active

    response = await client.post(
        "/v1/users/by_id/alice/reconsider",
        headers=auth(admin_token),
        json={"reason": "x"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("users:R17")
@pytest.mark.asyncio
async def test_reconsider_400_when_target_is_self(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    response = await client.post(
        "/v1/users/by_id/admin/reconsider",
        headers=auth(admin_token),
        json={"reason": "x"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CANNOT_RECONSIDER_SELF"


@pytest.mark.requirement("users:R17")
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_reason", ["", "x" * 501])
async def test_reconsider_422_when_reason_out_of_bounds(
    client: AsyncClient, db_session: AsyncSession, bad_reason: str
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    response = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": bad_reason},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# reapply — happy path and guards
# ---------------------------------------------------------------------------


async def _setup_reconsidered_user(
    client: AsyncClient, db_session: AsyncSession, username: str = "rosa"
) -> str:
    """Create the user, take them to ``pending``, then reconsider them
    back to ``registered``. Returns the user's auth token."""
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client, username=username)
    response = await client.post(
        f"/v1/users/by_id/{username}/reconsider",
        headers=auth(admin_token),
        json={"reason": "please fix the form"},
    )
    assert response.status_code == 201
    login = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": "regpass123"},
    )
    assert login.status_code == 200, login.text
    token: str = login.json()["accessToken"]
    return token


@pytest.mark.requirement("users:R19")
@pytest.mark.asyncio
async def test_reapply_updates_fields_and_closes_active_row(
    client: AsyncClient, db_session: AsyncSession
):
    token = await _setup_reconsidered_user(client, db_session)

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"firstName": "Rosa", "phone": "9999999999"},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.first_name == "Rosa"
    assert user.phone == "9999999999"
    # Status stays registered until submit-for-review
    assert user.status == UserStatus.registered.value

    row = (
        await db_session.execute(
            select(UserReviewRequest).where(UserReviewRequest.username == "rosa")
        )
    ).scalar_one()
    assert row.resolution == "resubmitted"
    assert row.resolved_at is not None
    assert row.resolved_by == "rosa"


@pytest.mark.requirement("users:R61")
@pytest.mark.asyncio
async def test_reapply_writes_audit_log(client: AsyncClient, db_session: AsyncSession):
    token = await _setup_reconsidered_user(client, db_session)

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"firstName": "Rosa"},
    )
    assert response.status_code == 200

    db_session.expire_all()
    entries = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "reapply",
                    AuditLog.actor_username == "rosa",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(entries)) == 1


@pytest.mark.requirement("users:R20")
@pytest.mark.asyncio
async def test_reapply_rejects_non_self(client: AsyncClient, db_session: AsyncSession):
    _ = await _setup_reconsidered_user(client, db_session, "rosa")
    other_token = await create_member_user(db_session, "alice")

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(other_token),
        json={"firstName": "X"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


@pytest.mark.requirement("users:R21")
@pytest.mark.asyncio
async def test_reapply_409_when_no_active_review_request(
    client: AsyncClient, db_session: AsyncSession
):
    """User in ``registered`` state with no active row (e.g. freshly
    registered, never reconsidered) must get 409."""
    token = await create_registered_user(db_session, "rosa")

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"firstName": "X"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_ACTIVE_REVIEW_REQUEST"


@pytest.mark.requirement("users:R21")
@pytest.mark.asyncio
async def test_reapply_409_when_status_not_registered(
    client: AsyncClient, db_session: AsyncSession
):
    """If somehow the user is pending (caller bypassed the workflow),
    reapply must reject with 409."""
    token = await _setup_reconsidered_user(client, db_session)

    # Manually flip rosa back to pending without using submit-for-review
    rosa = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    rosa.status = UserStatus.pending.value
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"firstName": "X"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_STATE"


@pytest.mark.requirement("users:R21")
@pytest.mark.asyncio
async def test_reapply_cannot_bypass_protected_fields_without_active_row(
    client: AsyncClient, db_session: AsyncSession
):
    """``reapply`` is the only self-serve path that accepts ``gender`` /
    ``dateOfBirthUtc`` (otherwise super-admin-only on ``PATCH /by_id/{u}``).
    That privilege is gated on an active review request: without one,
    the user must not be able to change those fields via this endpoint."""
    token = await create_registered_user(db_session, "rosa")
    # Commit so the row survives the rollback triggered by the 409 below.
    await db_session.commit()

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"gender": "female", "dateOfBirthUtc": 946684800000},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_ACTIVE_REVIEW_REQUEST"

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.gender is None
    assert user.date_of_birth is None


@pytest.mark.requirement("users:R19")
@pytest.mark.asyncio
async def test_reapply_with_active_row_permits_protected_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """Companion to the previous test: with an active review row, the
    user *can* update gender + DOB through reapply. This is the
    intentional grant; the active row is the authorisation."""
    token = await _setup_reconsidered_user(client, db_session)

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"gender": "female", "dateOfBirthUtc": 946684800000},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.gender == "female"
    assert user.date_of_birth == 946684800000


@pytest.mark.requirement("users:R22")
@pytest.mark.requirement("users:R9b")
@pytest.mark.asyncio
async def test_reapply_rejects_fields_outside_the_registration_subset(
    client: AsyncClient, db_session: AsyncSession
):
    """Only the registration-field subset is accepted. Anything else is now
    rejected outright (#325); it used to be discarded, so a caller sending
    ``bio`` got 200 and no bio."""
    token = await _setup_reconsidered_user(client, db_session)

    response = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(token),
        json={"firstName": "Rosa", "bio": "should be rejected"},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(
        err["type"] == "extra_forbidden" and err["loc"][-1] == "bio" for err in detail
    ), detail

    # The whole request is rejected, so the accepted field is not applied.
    db_session.expire_all()
    user = (
        await db_session.execute(select(User).where(User.username == "rosa"))
    ).scalar_one()
    assert user.bio is None


# ---------------------------------------------------------------------------
# approve / block — close active row with proper resolution
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R23")
@pytest.mark.asyncio
async def test_approve_closes_active_row_with_approved_resolution(
    client: AsyncClient, db_session: AsyncSession
):
    """Sequence: pending → reconsider (active row + status=registered) →
    submit-for-review (status=pending, row still active) → approve.
    The approve call should close the active row as 'approved'."""
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    r = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "please fix"},
    )
    assert r.status_code == 201

    rosa_login = await client.post(
        "/v1/auth/login",
        json={"username": "rosa", "password": "regpass123"},
    )
    rosa_token: str = rosa_login.json()["accessToken"]
    submit = await client.post(
        "/v1/users/me/submit-for-review", headers=auth(rosa_token)
    )
    assert submit.status_code == 200

    response = await client.post(
        "/v1/users/by_id/rosa/approve",
        headers=auth(admin_token),
        json={"resolutionReason": "all checks passed"},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(UserReviewRequest).where(UserReviewRequest.username == "rosa")
        )
    ).scalar_one()
    assert row.resolution == "approved"
    assert row.resolution_reason == "all checks passed"
    assert row.resolved_by == "admin"
    assert row.resolved_at is not None


@pytest.mark.requirement("users:R23")
@pytest.mark.asyncio
async def test_block_closes_active_row_with_blocked_resolution(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    r = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "please clarify"},
    )
    assert r.status_code == 201

    # Active row exists; block rosa with a resolutionReason
    response = await client.post(
        "/v1/users/by_id/rosa/block",
        headers=auth(admin_token),
        json={"resolutionReason": "spam account"},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(UserReviewRequest).where(UserReviewRequest.username == "rosa")
        )
    ).scalar_one()
    assert row.resolution == "blocked"
    assert row.resolution_reason == "spam account"
    assert row.resolved_by == "admin"
    assert row.resolved_at is not None


@pytest.mark.requirement("users:R23")
@pytest.mark.asyncio
async def test_block_without_body_still_closes_active_row(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    r = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "please clarify"},
    )
    assert r.status_code == 201

    response = await client.post(
        "/v1/users/by_id/rosa/block",
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(UserReviewRequest).where(UserReviewRequest.username == "rosa")
        )
    ).scalar_one()
    assert row.resolution == "blocked"
    assert row.resolution_reason is None


# ---------------------------------------------------------------------------
# Partial unique index
# ---------------------------------------------------------------------------


@pytest.mark.requirement("users:R16")
@pytest.mark.asyncio
async def test_at_most_one_active_row_per_user(
    client: AsyncClient, db_session: AsyncSession
):
    """Direct DB write attempting two active rows for the same user
    must fail (partial unique index)."""
    from sqlalchemy.exc import IntegrityError

    _ = await create_member_user(db_session, "rosa")
    db_session.add(
        UserReviewRequest(
            username="rosa",
            reason="r1",
            requested_by="rosa",
            created_at=1,
        )
    )
    await db_session.flush()
    db_session.add(
        UserReviewRequest(
            username="rosa",
            reason="r2",
            requested_by="rosa",
            created_at=2,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Round trip — exactly one user_approval notification at the end.
# ---------------------------------------------------------------------------


@pytest.mark.requirement("notifications:R38")
@pytest.mark.asyncio
async def test_round_trip_yields_single_user_approval_notification(
    client: AsyncClient, db_session: AsyncSession
):
    admin_token = await create_admin_user(db_session)
    _ = await _create_pending_user(db_session, client)

    # admin sees one user_approval row from initial submit-for-review
    db_session.expire_all()
    pre = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.pending_action_type == "user_approval",
                    Notification.pending_action_key == "rosa",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(pre)) == 1

    # Admin reconsiders
    r = await client.post(
        "/v1/users/by_id/rosa/reconsider",
        headers=auth(admin_token),
        json={"reason": "fix the form"},
    )
    assert r.status_code == 201

    # User reapplies
    rosa_login = await client.post(
        "/v1/auth/login",
        json={"username": "rosa", "password": "regpass123"},
    )
    rosa_token: str = rosa_login.json()["accessToken"]
    reapply = await client.patch(
        "/v1/users/by_id/rosa/reapply",
        headers=auth(rosa_token),
        json={"firstName": "Rosa"},
    )
    assert reapply.status_code == 200

    # User submits for review again
    submit = await client.post(
        "/v1/users/me/submit-for-review", headers=auth(rosa_token)
    )
    assert submit.status_code == 200

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.pending_action_type == "user_approval",
                    Notification.pending_action_key == "rosa",
                    Notification.username == "admin",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(list(rows)) == 1, (
        "exactly one user_approval notification — the reconsider should have "
        "deleted the original, and submit-for-review created a new one"
    )
