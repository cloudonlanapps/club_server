"""Semi-auto members who stop matching are kept, flagged and reported (#17).

groups_requirements.md R82–R88. A member stops matching here the way the
calendar makes them: the scan is run for a later day, or — where the API's
own reading of "today" is under test — their date of birth is corrected to
one outside today's window.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_today
from club_server.db.models.user import User
from club_server.services.scheduler import scan_group_eligibility
from club_server.utils import MS_PER_DAY, now_utc_ms

from .eligibility_helpers import EIGHTEEN, FIVE, edge_births, window_on
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
)
from .redesign_helpers import auth, notifications_for

NOTICE = "group.member_ineligible"
BAND = {"minAge": FIVE, "maxAge": EIGHTEEN, "strictAge": True}
TWO_DAYS = 2 * MS_PER_DAY


def births() -> dict[str, int]:
    return edge_births(window_on(club_today(), strict=True))


async def create_group(client: AsyncClient, token: str, **body: object) -> int:
    response = await client.post(
        "/v1/groups", json={"name": "Juniors", **body}, headers=auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add(client: AsyncClient, token: str, group_id: int, name: str) -> None:
    response = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/{name}", headers=auth(token)
    )
    assert response.status_code == 201, response.text


async def set_birth(db: AsyncSession, name: str, born: int) -> None:
    _ = await db.execute(
        update(User).where(User.username == name).values(date_of_birth=born)
    )
    await db.commit()
    db.expire_all()


async def members(client: AsyncClient, token: str, group_id: int) -> dict[str, bool]:
    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return {m["membername"]: m["eligible"] for m in response.json()}


async def group(client: AsyncClient, token: str, group_id: int) -> dict:
    response = await client.get(f"/v1/groups/by_id/{group_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def scan(db: AsyncSession, now: int | None = None) -> int:
    sent = await scan_group_eligibility(db, now_utc_ms() if now is None else now)
    await db.commit()
    return sent


async def semi_auto_with(
    client: AsyncClient, db: AsyncSession, admin: str, *names: str
) -> int:
    """A semi-auto group whose members are born on the oldest admitted day."""
    for name in names:
        _ = await create_member_user(db, name, date_of_birth=births()["oldest"])
    group_id = await create_group(client, admin, **BAND, semiAuto=True)
    for name in names:
        await add(client, admin, group_id, name)
    return group_id


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R82")
async def test_should_keep_and_flag_a_member_who_passes_the_maximum_age(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group_id = await semi_auto_with(client, db_session, admin, "amy", "ben")
    assert await members(client, admin, group_id) == {"amy": True, "ben": True}

    await set_birth(db_session, "amy", births()["too_old"])

    assert await members(client, admin, group_id) == {"amy": False, "ben": True}
    detail = await group(client, admin, group_id)
    assert {m["membername"]: m["eligible"] for m in detail["members"]} == {
        "amy": False,
        "ben": True,
    }


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R82")
async def test_should_flag_a_member_whose_gender_no_longer_matches(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy", gender="female")
    group_id = await create_group(client, admin, gender="female", semiAuto=True)
    await add(client, admin, group_id, "amy")

    _ = await db_session.execute(
        update(User).where(User.username == "amy").values(gender="male")
    )
    await db_session.commit()
    db_session.expire_all()

    assert await members(client, admin, group_id) == {"amy": False}


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R83")
async def test_should_report_how_many_members_no_longer_match(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group_id = await semi_auto_with(client, db_session, admin, "amy", "ben", "cat")
    assert (await group(client, admin, group_id))["ineligibleMemberCount"] == 0

    await set_birth(db_session, "amy", births()["too_old"])
    await set_birth(db_session, "cat", births()["too_young"])

    assert (await group(client, admin, group_id))["ineligibleMemberCount"] == 2
    listing = await client.get("/v1/groups", headers=auth(admin))
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["ineligibleMemberCount"] == 2
    assert listing.json()["items"][0]["memberCount"] == 3


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R84")
async def test_should_notify_admins_when_a_member_grows_out_of_the_band(
    client: AsyncClient, db_session: AsyncSession
):
    """Nothing is changed but the day: the scan runs two days later."""
    sudo = await create_admin_user(db_session)
    _ = await create_regular_admin_user(db_session, "boss")
    _ = await create_coach_user(db_session, "carl")
    group_id = await semi_auto_with(client, db_session, sudo, "amy", "ben")
    await set_birth(db_session, "ben", births()["oldest"] + 30 * MS_PER_DAY)
    assert await scan(db_session) == 0

    sent = await scan(db_session, now_utc_ms() + TWO_DAYS)

    assert sent == 2
    for recipient in ("admin", "boss"):
        notices = await notifications_for(db_session, recipient, NOTICE)
        assert len(notices) == 1, recipient
        data = notices[0].payload["data"]
        assert data["groupId"] == group_id
        assert data["groupName"] == "Juniors"
        assert data["membername"] == "amy"
    assert await notifications_for(db_session, "carl", NOTICE) == []
    assert await notifications_for(db_session, "amy", NOTICE) == []


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R85")
async def test_should_notify_once_however_many_scans_run(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await semi_auto_with(client, db_session, admin, "amy")
    await set_birth(db_session, "amy", births()["too_old"])

    assert await scan(db_session) == 1
    assert await scan(db_session) == 0
    assert await scan(db_session, now_utc_ms() + TWO_DAYS) == 0

    assert len(await notifications_for(db_session, "admin", NOTICE)) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R86")
async def test_should_report_afresh_a_member_who_matched_again_and_then_stopped(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group_id = await semi_auto_with(client, db_session, admin, "amy")
    await set_birth(db_session, "amy", births()["too_old"])
    assert await scan(db_session) == 1

    await set_birth(db_session, "amy", births()["oldest"])
    assert await members(client, admin, group_id) == {"amy": True}
    assert (await group(client, admin, group_id))["ineligibleMemberCount"] == 0
    assert await scan(db_session) == 0

    await set_birth(db_session, "amy", births()["too_old"])
    assert await scan(db_session) == 1

    assert len(await notifications_for(db_session, "admin", NOTICE)) == 2


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R86")
async def test_should_list_as_eligible_a_member_readmitted_by_relaxed_criteria(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group_id = await semi_auto_with(client, db_session, admin, "amy")
    await set_birth(db_session, "amy", births()["too_old"])
    assert await members(client, admin, group_id) == {"amy": False}

    relaxed = await client.patch(
        f"/v1/groups/by_id/{group_id}",
        json={"maxAge": {"years": 19}},
        headers=auth(admin),
    )
    assert relaxed.status_code == 200, relaxed.text

    assert await members(client, admin, group_id) == {"amy": True}


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R87")
async def test_should_never_flag_staff(client: AsyncClient, db_session: AsyncSession):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "carl")
    _ = await create_regular_admin_user(db_session, "boss")
    group_id = await create_group(client, admin, **BAND, semiAuto=True)
    await add(client, admin, group_id, "carl")
    await add(client, admin, group_id, "boss")

    assert await scan(db_session) == 0

    assert await members(client, admin, group_id) == {"carl": True, "boss": True}
    assert (await group(client, admin, group_id))["ineligibleMemberCount"] == 0
    assert await notifications_for(db_session, "admin", NOTICE) == []


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R88")
async def test_should_flag_nobody_in_manual_and_auto_groups(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "amy", date_of_birth=births()["oldest"])
    manual = await create_group(client, admin, name="Manual")
    await add(client, admin, manual, "amy")
    auto = await create_group(client, admin, name="Auto", **BAND)
    assert await members(client, admin, auto) == {"amy": True}

    await set_birth(db_session, "amy", births()["too_old"])
    assert await scan(db_session) == 0

    assert await members(client, admin, manual) == {"amy": True}
    assert await members(client, admin, auto) == {}
    for group_id in (manual, auto):
        assert (await group(client, admin, group_id))["ineligibleMemberCount"] == 0
    assert await notifications_for(db_session, "admin", NOTICE) == []


@pytest.mark.asyncio
@pytest.mark.requirement("groups:R88")
async def test_should_not_report_members_of_a_soft_deleted_group(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group_id = await semi_auto_with(client, db_session, admin, "amy")
    deleted = await client.delete(f"/v1/groups/by_id/{group_id}", headers=auth(admin))
    assert deleted.status_code == 200, deleted.text
    await set_birth(db_session, "amy", births()["too_old"])

    assert await scan(db_session) == 0

    assert await notifications_for(db_session, "admin", NOTICE) == []
