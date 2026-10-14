"""An event's and a group's eligibility take several genders (#25, #26,
eligibility R31–R35).

A criterion is a list of genders: a member passes when their gender is in
it, and an empty list is no criterion at all. The old single ``gender`` is
kept for a release as a list of one on writes, and as the one value on
reads when the list has exactly one.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import create_admin_user
from .redesign_helpers import (
    at,
    audit_rows,
    auth,
    create_oneoff,
    create_programme,
    create_venue,
    get_event,
    version_of,
)
from .test_event_eligibility import _register_member

THREE = ["male", "other", "prefer_not_to_say"]


async def _members(client: AsyncClient, admin: str, db_session: AsyncSession) -> None:
    """One active member of each gender, and one with none recorded."""
    for username, gender in (
        ("boy", "male"),
        ("girl", "female"),
        ("oth", "other"),
        ("pnts", "prefer_not_to_say"),
        ("nogender", None),
    ):
        _ = await _register_member(client, admin, username, db_session, gender=gender)


async def _event_eligible(client: AsyncClient, admin: str, event_id: int) -> set[str]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/eligible", headers=auth(admin)
    )
    assert response.status_code == 200, response.text
    return {u["username"] for u in response.json()}


async def _update_event(client: AsyncClient, admin: str, event_id: int, **body: object):
    body.setdefault("version", await version_of(client, admin, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}", json=body, headers=auth(admin)
    )


async def _create_group(client: AsyncClient, admin: str, **body: object):
    return await client.post("/v1/groups", json=body, headers=auth(admin))


# ---------------------------------------------------------------------------
# Events (#25)
# ---------------------------------------------------------------------------


@pytest.mark.requirement("eligibility:R31")
@pytest.mark.asyncio
async def test_should_store_and_return_genders_when_event_is_created_with_a_list(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    event = await create_oneoff(client, admin, venue, genders=THREE)

    assert event["genders"] == THREE
    assert event["gender"] is None
    assert (await get_event(client, admin, event["id"]))["genders"] == THREE


@pytest.mark.requirement("eligibility:R32")
@pytest.mark.asyncio
async def test_should_admit_only_members_whose_gender_is_listed_when_event_has_genders(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _members(client, admin, db_session)
    venue = await create_venue(client, admin)

    event = await create_oneoff(client, admin, venue, genders=THREE)

    eligible = await _event_eligible(client, admin, event["id"])
    assert {"boy", "oth", "pnts"} <= eligible
    assert "girl" not in eligible
    assert "nogender" not in eligible


@pytest.mark.requirement("eligibility:R31")
@pytest.mark.requirement("eligibility:R32")
@pytest.mark.asyncio
async def test_should_clear_the_criterion_when_event_genders_is_empty_or_null(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _members(client, admin, db_session)
    venue = await create_venue(client, admin)
    first = await create_oneoff(client, admin, venue, genders=["female"])
    second = await create_oneoff(
        client, admin, venue, start=at(days=3), genders=["female"], title="Second"
    )

    emptied = await _update_event(client, admin, first["id"], genders=[])
    nulled = await _update_event(client, admin, second["id"], genders=None)

    for response in (emptied, nulled):
        assert response.status_code == 200, response.text
        assert response.json()["genders"] == []
        assert response.json()["gender"] is None
        eligible = await _event_eligible(client, admin, response.json()["id"])
        assert {"boy", "girl", "oth", "pnts", "nogender"} <= eligible


@pytest.mark.requirement("eligibility:R33")
@pytest.mark.asyncio
async def test_should_reject_event_genders_when_a_value_is_unknown_or_repeated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event = await create_oneoff(client, admin, venue)

    for bad in (["male", "boys"], ["male", "male"]):
        created = await client.post(
            "/v1/events",
            json={
                "title": "Bad",
                "type": "oneOff",
                "venueId": venue,
                "startTimeUtc": event["startTimeUtc"],
                "endTimeUtc": event["endTimeUtc"],
                "genders": bad,
            },
            headers=auth(admin),
        )
        assert created.status_code == 422, (bad, created.text)
        updated = await _update_event(client, admin, event["id"], genders=bad)
        assert updated.status_code == 422, (bad, updated.text)

    assert (await get_event(client, admin, event["id"]))["genders"] == []


@pytest.mark.requirement("eligibility:R33")
@pytest.mark.asyncio
async def test_should_reject_an_event_write_that_sends_both_gender_and_genders(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event = await create_oneoff(client, admin, venue)

    response = await _update_event(
        client, admin, event["id"], gender="male", genders=["male", "other"]
    )

    assert response.status_code == 422, response.text
    assert (await get_event(client, admin, event["id"]))["genders"] == []


@pytest.mark.requirement("eligibility:R34")
@pytest.mark.asyncio
async def test_should_read_the_old_gender_as_a_list_of_one_when_event_is_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _members(client, admin, db_session)
    venue = await create_venue(client, admin)

    event = await create_oneoff(client, admin, venue, gender="female")

    assert event["genders"] == ["female"]
    assert event["gender"] == "female"
    assert await _event_eligible(client, admin, event["id"]) >= {"girl"}
    assert "boy" not in await _event_eligible(client, admin, event["id"])
    cleared = await _update_event(client, admin, event["id"], gender=None)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["genders"] == []
    assert cleared.json()["gender"] is None


@pytest.mark.requirement("eligibility:R31")
@pytest.mark.asyncio
async def test_should_correct_programme_genders_and_audit_both_lists(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, genders=["male"])

    corrected = await client.patch(
        f"/v1/events/by_id/{programme['id']}/correction",
        json={
            "version": await version_of(client, admin, programme["id"]),
            "genders": THREE,
        },
        headers=auth(admin),
    )

    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["genders"] == THREE
    rows = await audit_rows(db_session, "correct_event")
    assert len(rows) == 1
    assert '"genders"' in rows[0].details
    assert '"old": ["male"]' in rows[0].details
    assert '"prefer_not_to_say"' in rows[0].details


# ---------------------------------------------------------------------------
# Groups (#26)
# ---------------------------------------------------------------------------


@pytest.mark.requirement("eligibility:R31")
@pytest.mark.requirement("eligibility:R32")
@pytest.mark.asyncio
async def test_should_hold_only_listed_genders_when_auto_group_has_genders(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _members(client, admin, db_session)

    created = await _create_group(client, admin, name="Mixed", genders=THREE)

    assert created.status_code == 201, created.text
    assert created.json()["genders"] == THREE
    assert created.json()["gender"] is None
    assert created.json()["kind"] == "auto"
    members = await client.get(
        f"/v1/groups/by_id/{created.json()['id']}/members", headers=auth(admin)
    )
    names = {m["membername"] for m in members.json()}
    assert {"boy", "oth", "pnts"} <= names
    assert "girl" not in names and "nogender" not in names


@pytest.mark.requirement("eligibility:R32")
@pytest.mark.asyncio
async def test_should_follow_genders_when_semi_auto_group_lists_and_adds_members(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _members(client, admin, db_session)
    created = await _create_group(
        client, admin, name="Semi", genders=["female", "other"], semiAuto=True
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    eligible = await client.get(
        f"/v1/groups/by_id/{group_id}/eligible", headers=auth(admin)
    )
    names = {u["username"] for u in eligible.json()}
    assert {"girl", "oth"} <= names
    assert not names & {"boy", "pnts", "nogender"}
    refused = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/boy", headers=auth(admin)
    )
    assert refused.status_code == 422, refused.text
    added = await client.post(
        f"/v1/groups/by_id/{group_id}/members/byname/oth", headers=auth(admin)
    )
    assert added.status_code == 201, added.text


@pytest.mark.requirement("eligibility:R35")
@pytest.mark.asyncio
async def test_should_be_manual_when_group_genders_is_empty_and_no_age_bound(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await _create_group(client, admin, name="Auto", genders=["male"])
    assert created.json()["kind"] == "auto"

    updated = await client.patch(
        f"/v1/groups/by_id/{created.json()['id']}",
        json={"genders": []},
        headers=auth(admin),
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["genders"] == []
    assert updated.json()["kind"] == "manual"
    empty = await _create_group(client, admin, name="None", genders=[])
    assert empty.json()["kind"] == "manual"


@pytest.mark.requirement("eligibility:R33")
@pytest.mark.asyncio
async def test_should_reject_group_genders_when_a_value_is_unknown_or_repeated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group = await _create_group(client, admin, name="G")

    for bad in (["girls"], ["female", "female"]):
        created = await _create_group(client, admin, name="Bad", genders=bad)
        assert created.status_code == 422, (bad, created.text)
        updated = await client.patch(
            f"/v1/groups/by_id/{group.json()['id']}",
            json={"genders": bad},
            headers=auth(admin),
        )
        assert updated.status_code == 422, (bad, updated.text)


@pytest.mark.requirement("eligibility:R34")
@pytest.mark.asyncio
async def test_should_read_the_old_gender_as_a_list_of_one_when_group_is_written(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    created = await _create_group(client, admin, name="Girls", gender="female")

    assert created.status_code == 201, created.text
    assert created.json()["genders"] == ["female"]
    assert created.json()["gender"] == "female"
    detail = await client.get(
        f"/v1/groups/by_id/{created.json()['id']}", headers=auth(admin)
    )
    assert detail.json()["genders"] == ["female"]
    assert detail.json()["gender"] == "female"
