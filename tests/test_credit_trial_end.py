"""The removal that ends a trial is a complete removal (#447, R52, R52a, R52b).

When a mark spends the last of a member's trial credit, the enrollment is
removed. That removal stamps ``withdrawn_at`` like every other departure,
names its reason, writes an audit row, tells the member, and tells the
caller who marked the register.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.attendance import AttendanceRecord
from club_server.db.models.enrollment import Enrollment

from .credit_helpers import open_account
from .helpers import create_admin_user, create_member_user, create_regular_admin_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    at,
    audit_rows,
    auth,
    backdate_enrollment,
    create_programme,
    create_venue,
    mark,
    notifications_for,
)

TRIAL_ENDED = "trialCreditExhausted"


async def _trial_programme(
    client: AsyncClient, db_session: AsyncSession, admin: str, *, credits: int
) -> tuple[int, int]:
    """A daily programme five days old, with ``skater`` on a funded trial."""
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    _ = await open_account(
        client,
        admin,
        "skater",
        credits=credits,
        event_id=programme["id"],
        is_trial=True,
    )
    trial = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign-trial",
        json={"membername": "skater"},
        headers=auth(admin),
    )
    assert trial.status_code == 204, trial.text
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    return programme["id"], start


async def _my_enrollment(
    client: AsyncClient, token: str, event_id: int
) -> dict[str, object]:
    """The member's own view of their enrollment."""
    response = await client.get(
        f"/v1/myevents/by_id/skater/{event_id}/enrollments", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _enrollment_row(db_session: AsyncSession, event_id: int) -> Enrollment:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == "skater"
            )
        )
    ).scalar_one()


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R52")
@pytest.mark.asyncio
async def test_should_stamp_withdrawal_and_reason_when_trial_credit_exhausted(
    client: AsyncClient, db_session: AsyncSession
):
    """R52: the ended trial is a departure, dated and explained."""
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    event_id, start = await _trial_programme(client, db_session, admin, credits=1)
    before = at()

    marked = await mark(client, admin, event_id, start, "skater")

    assert marked.status_code == 200, marked.text
    mine = await _my_enrollment(client, member, event_id)
    assert mine["status"] == "removed"
    assert mine["isTrial"] is True
    assert mine["withdrawalReason"] == TRIAL_ENDED
    withdrawn_at = mine["withdrawnAtUtc"]
    assert isinstance(withdrawn_at, int) and withdrawn_at >= before
    theirs = await _my_enrollment(client, admin, event_id)
    assert theirs["withdrawnAtUtc"] == withdrawn_at


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R52b")
@pytest.mark.asyncio
@pytest.mark.requirement("credit:R52a")
async def test_should_write_audit_row_when_trial_credit_exhausted(
    client: AsyncClient, db_session: AsyncSession
):
    """R52a, R52b: a system action, recorded with the coach who triggered it."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    event_id, start = await _trial_programme(client, db_session, admin, credits=1)

    marked = await mark(client, admin, event_id, start, "skater")

    assert marked.status_code == 200, marked.text
    rows = await audit_rows(db_session, "enrollment_removed")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_username == "system"
    assert row.target_username == "skater"
    assert row.resource_type == "event"
    assert row.resource_id == str(event_id)
    details = json.loads(row.details)
    assert details["reason"] == TRIAL_ENDED
    assert details["triggeredBy"] == "admin"
    assert details["occurrenceTimeUtc"] == start


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R52")
@pytest.mark.asyncio
async def test_should_report_trial_ended_when_mark_spends_last_trial_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """The coach learns from the response, not from the next refresh."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    event_id, start = await _trial_programme(client, db_session, admin, credits=2)

    first = await mark(client, admin, event_id, start, "skater")
    assert first.status_code == 200, first.text
    assert first.json()["trialEnded"] == []

    second = await mark(client, admin, event_id, start + DAY_MS, "skater")

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["marked"] == [{"membername": "skater", "status": "present"}]
    assert body["trialEnded"] == [{"membername": "skater"}]
    assert body["refused"] == []


@pytest.mark.requirement("notifications:R68")
@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R52")
@pytest.mark.asyncio
async def test_should_notify_member_when_trial_credit_exhausted(
    client: AsyncClient, db_session: AsyncSession
):
    """The member is told their trial is over, with its start and end."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    event_id, start = await _trial_programme(client, db_session, admin, credits=1)

    marked = await mark(client, admin, event_id, start, "skater")

    assert marked.status_code == 200, marked.text
    withdrawn_at = (await _enrollment_row(db_session, event_id)).withdrawn_at
    assert withdrawn_at is not None
    rows = await notifications_for(db_session, "skater", "enrollment.trial_ended")
    assert rows, "no enrollment.trial_ended notification"
    for row in rows:
        data = row.payload["data"]
        assert data["eventId"] == event_id
        assert data["enrolledAtUtc"] == start - HOUR_MS
        assert data["withdrawnAtUtc"] == withdrawn_at
    assert (
        await notifications_for(db_session, "skater", "enrollment.cancelled_admin")
        == []
    )


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.requirement("credit:R52")
@pytest.mark.asyncio
async def test_should_not_cover_later_past_occurrence_when_trial_ended(
    client: AsyncClient, db_session: AsyncSession
):
    """An ended trial covers nothing after it ended, once that is in the past.

    The removal is aged to just after the session that ended it, as if the
    coach had marked that session on the day. A later session is then outside
    the member's stint: it is refused as an enrollment error rather than for
    credit, and a record on it is not part of the member's history.
    """
    admin = await create_admin_user(db_session)
    regular_admin = await create_regular_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    event_id, start = await _trial_programme(client, db_session, admin, credits=1)
    ended = await mark(client, admin, event_id, start, "skater")
    assert ended.status_code == 200, ended.text
    enrollment = await _enrollment_row(db_session, event_id)
    assert enrollment.withdrawn_at is not None
    enrollment.withdrawn_at = start + 2 * HOUR_MS
    later = start + 2 * DAY_MS
    db_session.add(
        AttendanceRecord(
            event_id=event_id,
            occurrence_time_utc=later,
            membername="skater",
            status="onLeave",
            recorded_at=start,
        )
    )
    await db_session.commit()

    refused = await mark(client, regular_admin, event_id, start + 3 * DAY_MS, "skater")

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "INVALID_STATE"
    history = await client.get(
        "/v1/myevents/by_id/skater/attendance",
        params={"fromTimeUtc": start - HOUR_MS, "toTimeUtc": at()},
        headers=auth(member),
    )
    assert history.status_code == 200, history.text
    assert [r["occurrenceTimeUtc"] for r in history.json()] == [start]


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_keep_admin_reason_and_notice_when_admin_removes_trial_member(
    client: AsyncClient, db_session: AsyncSession
):
    """An admin removing a trial member is not a trial ending."""
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    event_id, _ = await _trial_programme(client, db_session, admin, credits=2)

    removed = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={
            "membernames": ["skater"],
            "reason": "Not a fit",
            "creditDisposition": {
                "penalty": 2,
                "validFromUtc": at(days=-1),
                "validUntilUtc": at(days=90),
                "reason": "Trial cut short",
            },
        },
        headers=auth(admin),
    )

    assert removed.status_code == 204, removed.text
    mine = await _my_enrollment(client, member, event_id)
    assert mine["status"] == "removed"
    assert mine["withdrawalReason"] == "Not a fit"
    assert await notifications_for(db_session, "skater", "enrollment.cancelled_admin")
    assert await notifications_for(db_session, "skater", "enrollment.trial_ended") == []


@pytest.mark.usefixtures("credit_enabled")
@pytest.mark.asyncio
async def test_should_report_no_trial_ended_when_member_is_not_on_trial(
    client: AsyncClient, db_session: AsyncSession
):
    """A paying member's last credit blocks them; it ends nothing."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    _ = await open_account(client, admin, "skater", credits=1, event_id=programme["id"])
    assigned = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )
    assert assigned.status_code == 204, assigned.text
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)

    marked = await mark(client, admin, programme["id"], start, "skater")

    assert marked.status_code == 200, marked.text
    assert marked.json()["trialEnded"] == []
    assert await audit_rows(db_session, "enrollment_removed") == []


@pytest.mark.requirement("credit:R97")
@pytest.mark.asyncio
async def test_should_report_empty_trial_ended_when_deployment_has_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R97: the response shape does not vary with configuration."""
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    trial = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/assign-trial",
        json={"membername": "skater"},
        headers=auth(admin),
    )
    assert trial.status_code == 204, trial.text
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)

    marked = await mark(client, admin, programme["id"], start, "skater")

    assert marked.status_code == 200, marked.text
    assert marked.json()["trialEnded"] == []
