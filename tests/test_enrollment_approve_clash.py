"""Approving a join request runs the member clash gate (#466, enrollment R30).

The gate counts only enrolled statuses, so a request does not clash with
another request; approval is where the member becomes enrolled, and it is
where the gate must run, as it does for assign and accept.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .enrollment_rejoin_helpers import (
    admin_record,
    approve,
    member_record,
    request_join,
)
from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    HOUR_MS,
    assign,
    at,
    create_camp,
    create_programme,
    create_venue,
    weekday_code,
)


@pytest.mark.requirement("enrollment:R30")
@pytest.mark.asyncio
async def test_should_refuse_approval_when_request_clashes_with_approved_programme(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "other_organizer")
    member = await create_member_user(db_session, "skater")
    start = at(days=2)
    rrule = f"FREQ=WEEKLY;BYDAY={weekday_code(start)}"
    first = await create_programme(
        client,
        admin,
        await create_venue(client, admin, "Rink A"),
        start=start,
        end=start + HOUR_MS,
        rrule=rrule,
    )
    second = await create_programme(
        client,
        admin,
        await create_venue(client, admin, "Rink B"),
        start=start,
        end=start + HOUR_MS,
        rrule=rrule,
        organizerName="other_organizer",
    )
    for event in (first, second):
        requested = await request_join(client, member, event["id"], "skater")
        assert requested.status_code == 204, requested.text
    assert (await approve(client, admin, first["id"], "skater")).status_code == 204

    response = await approve(client, admin, second["id"], "skater")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "TIME_CONFLICT"
    assert detail["conflicting_event_ids"] == [first["id"]]
    record = await admin_record(client, admin, second["id"], "skater")
    assert record["status"] == "requested"
    mine = await member_record(client, member, second["id"], "skater")
    assert mine["status"] == "requested"


@pytest.mark.requirement("enrollment:R30")
@pytest.mark.asyncio
async def test_should_approve_request_when_overlap_is_only_with_a_camp(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "skater")
    start = at(days=2)
    camp = await create_camp(
        client, admin, await create_venue(client, admin, "Rink A"), start=start
    )
    programme = await create_programme(
        client,
        admin,
        await create_venue(client, admin, "Rink B"),
        start=start,
        end=start + HOUR_MS,
        rrule=f"FREQ=WEEKLY;BYDAY={weekday_code(start)}",
    )
    assert (await assign(client, admin, camp["id"], "skater")).status_code == 204
    requested = await request_join(client, member, programme["id"], "skater")
    assert requested.status_code == 204, requested.text

    response = await approve(client, admin, programme["id"], "skater")

    assert response.status_code == 204, response.text
    record = await admin_record(client, admin, programme["id"], "skater")
    assert record["status"] == "accepted"
    mine = await member_record(client, member, programme["id"], "skater")
    assert mine["status"] == "accepted"
