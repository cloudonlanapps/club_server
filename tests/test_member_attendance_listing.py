"""A member's own attendance listing is filtered temporally (#379).

``docs/attendance_requirements.md`` R37 defers to R20: a past record is the
member's if their enrollment covered it at the time, whatever the enrollment's
status is today. Each test is one of the cases #379 names.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user, create_member_user
from .redesign_helpers import (
    DAY_MS,
    HOUR_MS,
    assign,
    at,
    auth,
    backdate_enrollment,
    create_programme,
    create_venue,
    mark,
)


async def _my_attendance(
    client: AsyncClient, token: str, member: str, from_ms: int, to_ms: int
) -> list[tuple[int, str]]:
    response = await client.get(
        f"/v1/myevents/by_id/{member}/attendance",
        params={"fromTimeUtc": from_ms, "toTimeUtc": to_ms},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return sorted((r["occurrenceTimeUtc"], r["status"]) for r in response.json())


async def _withdraw(
    client: AsyncClient, admin: str, member_token: str, event_id: int
) -> None:
    asked = await client.post(
        f"/v1/myevents/by_id/skater/{event_id}/enrollments/withdraw",
        json={"reason": "Moving away"},
        headers=auth(member_token),
    )
    assert asked.status_code == 204, asked.text
    approved = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )
    assert approved.status_code == 204, approved.text


async def _programme_with_marked_history(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, str, int, int]:
    """A daily programme five days old with the member marked present on days 1–3."""
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start - HOUR_MS)
    for day in range(1, 4):
        marked = await mark(
            client, admin, programme["id"], start + day * DAY_MS, "skater"
        )
        assert marked.status_code == 200, marked.text
    return admin, member, programme["id"], start


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_list_past_attendance_when_member_has_withdrawn(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, programme_id, start = await _programme_with_marked_history(
        client, db_session
    )
    await _withdraw(client, admin, member, programme_id)

    listed = await _my_attendance(
        client, member, "skater", start - HOUR_MS, start + 10 * DAY_MS
    )

    assert listed == [
        (start + DAY_MS, "present"),
        (start + 2 * DAY_MS, "present"),
        (start + 3 * DAY_MS, "present"),
    ]


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_omit_attendance_after_withdrawn_at_when_member_has_withdrawn(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, programme_id, start = await _programme_with_marked_history(
        client, db_session
    )
    future_slot = start + 7 * DAY_MS
    leave = await client.post(
        f"/v1/myevents/by_id/skater/{programme_id}/occurrences/{future_slot}/leave/request",
        json={"reason": "Holiday"},
        headers=auth(member),
    )
    assert leave.status_code == 204, leave.text
    await _withdraw(client, admin, member, programme_id)

    listed = await _my_attendance(
        client, member, "skater", start - HOUR_MS, start + 10 * DAY_MS
    )

    assert (future_slot, "onLeaveRequested") not in listed
    assert len(listed) == 3


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_include_attendance_at_withdrawn_at_boundary(
    client: AsyncClient, db_session: AsyncSession
):
    from sqlalchemy import select

    from club_server.db.models.enrollment import Enrollment

    admin, member, programme_id, start = await _programme_with_marked_history(
        client, db_session
    )
    await _withdraw(client, admin, member, programme_id)
    row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == programme_id, Enrollment.membername == "skater"
            )
        )
    ).scalar_one()
    row.withdrawn_at = start + 3 * DAY_MS
    await db_session.commit()

    listed = await _my_attendance(
        client, member, "skater", start - HOUR_MS, start + 10 * DAY_MS
    )

    assert (start + 3 * DAY_MS, "present") in listed
    assert len(listed) == 3


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_omit_attendance_before_enrolled_at_when_member_joined_mid_series(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    start = at(days=-5)
    programme = await create_programme(client, admin, venue, start=start)
    assert (await assign(client, admin, programme["id"], "skater")).status_code == 204
    await backdate_enrollment(db_session, programme["id"], "skater", start + 2 * DAY_MS)
    before_joining = await mark(
        client, admin, programme["id"], start + DAY_MS, "skater"
    )
    assert before_joining.status_code == 200, before_joining.text
    after_joining = await mark(
        client, admin, programme["id"], start + 3 * DAY_MS, "skater"
    )
    assert after_joining.status_code == 200, after_joining.text

    listed = await _my_attendance(
        client, member, "skater", start - HOUR_MS, start + 10 * DAY_MS
    )

    assert listed == [(start + 3 * DAY_MS, "present")]


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_list_attendance_when_member_only_invited(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    venue = await create_venue(client, admin)
    slot = at(minutes=10)
    programme = await create_programme(client, admin, venue, start=slot)
    invited = await client.post(
        f"/v1/events/by_id/{programme['id']}/enrollments/invite",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )
    assert invited.status_code == 204, invited.text
    marked = await mark(client, admin, programme["id"], slot, "skater")
    assert marked.status_code == 200, marked.text

    listed = await _my_attendance(
        client, member, "skater", slot - HOUR_MS, slot + HOUR_MS
    )

    assert listed == [(slot, "present")]


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_list_attendance_when_member_removed_by_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, programme_id, start = await _programme_with_marked_history(
        client, db_session
    )
    removed = await client.post(
        f"/v1/events/by_id/{programme_id}/enrollments/remove",
        json={"membernames": ["skater"]},
        headers=auth(admin),
    )
    assert removed.status_code == 204, removed.text

    listed = await _my_attendance(
        client, member, "skater", start - HOUR_MS, start + 10 * DAY_MS
    )

    assert len(listed) == 3


@pytest.mark.requirement("attendance:R37")
@pytest.mark.asyncio
async def test_should_return_same_records_to_staff_and_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin, member, programme_id, start = await _programme_with_marked_history(
        client, db_session
    )
    await _withdraw(client, admin, member, programme_id)

    mine = await _my_attendance(
        client, member, "skater", start - HOUR_MS, start + 10 * DAY_MS
    )
    staff = await client.get(
        "/v1/events/occurrences/attendance",
        params={"fromTimeUtc": start - HOUR_MS, "toTimeUtc": start + 10 * DAY_MS},
        headers=auth(admin),
    )
    assert staff.status_code == 200, staff.text
    theirs = sorted(
        (r["occurrenceTimeUtc"], r["status"])
        for r in staff.json()
        if r["membername"] == "skater"
    )

    assert mine == theirs
    assert len(mine) == 3
