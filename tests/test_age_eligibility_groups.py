"""Age-based eligibility on groups (#16, eligibility_requirements.md)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_today

from .eligibility_helpers import EIGHTEEN, FIVE, edge_births, window_on
from .helpers import create_admin_user, create_member_user
from .redesign_helpers import auth

BAND = {"minAge": FIVE, "maxAge": EIGHTEEN}


async def create_group(
    client: AsyncClient, token: str, expected_status: int = 201, **body: object
) -> dict:
    payload: dict[str, object] = {"name": "Juniors", **body}
    response = await client.post("/v1/groups", json=payload, headers=auth(token))
    assert response.status_code == expected_status, response.text
    return response.json()


async def get_group(client: AsyncClient, token: str, group_id: int) -> dict:
    response = await client.get(f"/v1/groups/by_id/{group_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def member_names(client: AsyncClient, token: str, group_id: int) -> set[str]:
    response = await client.get(
        f"/v1/groups/by_id/{group_id}/members", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return {m["membername"] for m in response.json()}


async def seed_edges(db_session: AsyncSession, *, strict: bool) -> dict[str, str]:
    """A member born on and just outside each end of today's window."""
    births = edge_births(window_on(club_today(), strict=strict))
    return {
        name: await create_member_user(db_session, name, date_of_birth=born)
        for name, born in births.items()
    }


# --- R1, R2, R10, R11, R19: storing the band ------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R1")
async def test_should_store_the_age_band_when_a_group_is_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    created = await create_group(client, admin, **BAND, strictAge=True)

    fetched = await get_group(client, admin, created["id"])
    assert fetched["minAge"] == FIVE
    assert fetched["maxAge"] == EIGHTEEN
    assert fetched["strictAge"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R1")
async def test_should_clear_a_group_bound_on_null_and_keep_an_omitted_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group = await create_group(client, admin, **BAND, strictAge=True)

    patched = await client.patch(
        f"/v1/groups/by_id/{group['id']}", json={"maxAge": None}, headers=auth(admin)
    )
    assert patched.status_code == 200, patched.text

    fetched = await get_group(client, admin, group["id"])
    assert fetched["maxAge"] is None
    assert fetched["minAge"] == FIVE
    assert fetched["strictAge"] is True
    assert fetched["dobOnOrAfterUtc"] is None
    assert fetched["dobOnOrBeforeUtc"] is not None


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R2")
@pytest.mark.parametrize("age", [{"years": 5, "days": 31}, {"years": 200}, "five"])
async def test_should_refuse_a_group_whose_age_is_malformed(
    client: AsyncClient, db_session: AsyncSession, age: object
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    _ = await create_group(client, admin, 422, maxAge=age)

    listing = await client.get("/v1/groups", headers=auth(admin))
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R10")
async def test_should_refuse_a_group_whose_minimum_age_exceeds_its_maximum(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group = await create_group(client, admin, **BAND)
    await db_session.commit()

    _ = await create_group(client, admin, 422, name="Bad", minAge=EIGHTEEN, maxAge=FIVE)
    patched = await client.patch(
        f"/v1/groups/by_id/{group['id']}",
        json={"maxAge": {"years": 4}},
        headers=auth(admin),
    )
    assert patched.status_code == 422, patched.text

    listing = await client.get("/v1/groups", headers=auth(admin))
    assert listing.json()["total"] == 1
    assert (await get_group(client, admin, group["id"]))["maxAge"] == EIGHTEEN


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R11")
@pytest.mark.parametrize("field", ["dobOnOrAfterUtc", "dobOnOrBeforeUtc"])
async def test_should_refuse_a_date_of_birth_bound_on_a_group_write(
    client: AsyncClient, db_session: AsyncSession, field: str
):
    admin = await create_admin_user(db_session)
    group = await create_group(client, admin, **BAND)
    await db_session.commit()

    _ = await create_group(
        client, admin, 422, name="Old style", **{field: 1262304000000}
    )
    patched = await client.patch(
        f"/v1/groups/by_id/{group['id']}",
        json={field: 1262304000000},
        headers=auth(admin),
    )
    assert patched.status_code == 422, patched.text

    listing = await client.get("/v1/groups", headers=auth(admin))
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R19")
async def test_should_derive_the_kind_from_an_age_bound_alone(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    manual = await create_group(client, admin, name="Manual")
    auto = await create_group(client, admin, name="Auto", maxAge=EIGHTEEN)
    semi = await create_group(client, admin, name="Semi", minAge=FIVE, semiAuto=True)

    assert (await get_group(client, admin, manual["id"]))["kind"] == "manual"
    assert (await get_group(client, admin, auto["id"]))["kind"] == "auto"
    assert (await get_group(client, admin, semi["id"]))["kind"] == "semi_auto"

    cleared = await client.patch(
        f"/v1/groups/by_id/{auto['id']}", json={"maxAge": None}, headers=auth(admin)
    )
    assert cleared.status_code == 200, cleared.text
    assert (await get_group(client, admin, auto["id"]))["kind"] == "manual"


# --- R4, R12: today's window, and what reports it -------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R4")
@pytest.mark.requirement("eligibility:R12")
@pytest.mark.parametrize("strict", [True, False])
async def test_should_report_a_groups_window_counted_from_today(
    client: AsyncClient, db_session: AsyncSession, strict: bool
):
    admin = await create_admin_user(db_session)
    today = club_today()
    window = window_on(today, strict=strict)

    group = await create_group(client, admin, **BAND, strictAge=strict)

    detail = await get_group(client, admin, group["id"])
    listing = await client.get("/v1/groups", headers=auth(admin))
    assert listing.status_code == 200, listing.text
    for view in (detail, listing.json()["items"][0]):
        assert view["minAge"] == FIVE
        assert view["maxAge"] == EIGHTEEN
        assert view["strictAge"] is strict
        assert view["dobOnOrAfterUtc"] == window.dob_on_or_after_utc
        assert view["dobOnOrBeforeUtc"] == window.dob_on_or_before_utc
        assert view["eligibilityReferenceDayUtc"] == today


# --- R16, R17, R18: what checks the window --------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R16")
@pytest.mark.parametrize("strict", [True, False])
async def test_should_make_an_auto_groups_members_the_users_inside_todays_window(
    client: AsyncClient, db_session: AsyncSession, strict: bool
):
    admin = await create_admin_user(db_session)
    _ = await seed_edges(db_session, strict=strict)
    _ = await create_member_user(db_session, "undated")

    group = await create_group(client, admin, **BAND, strictAge=strict)

    assert await member_names(client, admin, group["id"]) == {"oldest", "youngest"}
    listed = await client.get("/v1/groups", headers=auth(admin))
    assert listed.json()["items"][0]["memberCount"] == 2


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R17")
@pytest.mark.parametrize("strict", [True, False])
async def test_should_add_to_a_semi_auto_group_only_users_inside_todays_window(
    client: AsyncClient, db_session: AsyncSession, strict: bool
):
    admin = await create_admin_user(db_session)
    _ = await seed_edges(db_session, strict=strict)
    group = await create_group(client, admin, **BAND, strictAge=strict, semiAuto=True)
    await db_session.commit()
    url = f"/v1/groups/by_id/{group['id']}/members/byname/"

    for name in ("oldest", "youngest"):
        added = await client.post(url + name, headers=auth(admin))
        assert added.status_code == 201, (name, added.text)
    for name in ("too_old", "too_young"):
        refused = await client.post(url + name, headers=auth(admin))
        assert refused.status_code == 422, (name, refused.text)
        assert refused.json()["detail"]["code"] == "NOT_ELIGIBLE"

    assert await member_names(client, admin, group["id"]) == {"oldest", "youngest"}


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R17")
async def test_should_list_and_accept_requests_for_a_semi_auto_group_by_todays_window(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    tokens = await seed_edges(db_session, strict=False)
    group = await create_group(client, admin, **BAND, semiAuto=True)
    await db_session.commit()

    eligible = await client.get(
        f"/v1/groups/by_id/{group['id']}/eligible", headers=auth(admin)
    )
    assert eligible.status_code == 200, eligible.text
    names = {u["username"] for u in eligible.json()}
    assert {"oldest", "youngest"} <= names
    assert not names & {"too_old", "too_young"}

    joined = await client.post(
        f"/v1/mygroups/by_id/youngest/join/{group['id']}",
        headers=auth(tokens["youngest"]),
    )
    assert joined.status_code in (200, 201), joined.text
    requests = await client.get(
        f"/v1/groups/by_id/{group['id']}/requests", headers=auth(admin)
    )
    assert [r["username"] for r in requests.json()] == ["youngest"]

    refused = await client.post(
        f"/v1/mygroups/by_id/too_young/join/{group['id']}",
        headers=auth(tokens["too_young"]),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "NOT_ELIGIBLE"

    approved = await client.post(
        f"/v1/groups/by_id/{group['id']}/requests/{requests.json()[0]['id']}/approve",
        headers=auth(admin),
    )
    assert approved.status_code == 200, approved.text
    assert await member_names(client, admin, group["id"]) == {"youngest"}


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R18")
async def test_should_check_a_new_band_against_a_semi_auto_groups_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await seed_edges(db_session, strict=True)
    group = await create_group(client, admin, **BAND, strictAge=True, semiAuto=True)
    added = await client.post(
        f"/v1/groups/by_id/{group['id']}/members/byname/oldest", headers=auth(admin)
    )
    assert added.status_code == 201, added.text

    refused = await client.patch(
        f"/v1/groups/by_id/{group['id']}",
        json={"maxAge": {"years": 17}},
        headers=auth(admin),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "MEMBERS_INELIGIBLE"
    assert (await get_group(client, admin, group["id"]))["maxAge"] == EIGHTEEN

    widened = await client.patch(
        f"/v1/groups/by_id/{group['id']}",
        json={"maxAge": {"years": 19}},
        headers=auth(admin),
    )
    assert widened.status_code == 200, widened.text
    assert (await get_group(client, admin, group["id"]))["maxAge"] == {
        "years": 19,
        "months": 0,
        "days": 0,
    }
