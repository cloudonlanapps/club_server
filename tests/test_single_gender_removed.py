"""The single ``gender`` criterion is gone from the API (#54).

``docs/eligibility_requirements.md`` R34. Release 1.0.0 carried it next to
``genders`` (#25, #26); from here an event's or a group's criterion is
``genders`` alone. A write that still sends ``gender`` is refused with 422
naming the field, so a client cannot clear a several-gender criterion by
sending ``gender: null``, and no response carries it, so none can read a
girls-and-other event as open to everyone.

The member's own ``gender`` on users and profiles is a different field and
is not touched.
"""

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.utils import generate_event_public_id

from .helpers import create_admin_user
from .redesign_helpers import (
    auth,
    create_oneoff,
    create_programme,
    create_venue,
    get_event,
    version_of,
)

TWO = ["female", "other"]


def _refuses_gender(response: Response) -> bool:
    if response.status_code != 422:
        return False
    detail = response.json()["detail"]
    return any(
        err["type"] == "extra_forbidden" and err["loc"][-1] == "gender"
        for err in detail
    )


@pytest.mark.requirement("eligibility:R34")
@pytest.mark.asyncio
async def test_should_refuse_event_writes_when_they_send_gender(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    programme = await create_programme(client, admin, venue, genders=TWO)
    pid = programme["id"]

    created = await client.post(
        "/v1/events",
        json={
            "title": "Girls",
            "type": "oneOff",
            "venueId": venue,
            "startTimeUtc": programme["startTimeUtc"] + 3 * 60 * 60 * 1000,
            "endTimeUtc": programme["endTimeUtc"] + 3 * 60 * 60 * 1000,
            "gender": "female",
        },
        headers=auth(admin),
    )
    updated = await client.patch(
        f"/v1/events/by_id/{pid}",
        json={"version": await version_of(client, admin, pid), "gender": None},
        headers=auth(admin),
    )
    corrected = await client.patch(
        f"/v1/events/by_id/{pid}/correction",
        json={"version": await version_of(client, admin, pid), "gender": None},
        headers=auth(admin),
    )

    for response in (created, updated, corrected):
        assert _refuses_gender(response), response.text
    listed = await client.get("/v1/events", headers=auth(admin))
    assert [e["id"] for e in listed.json()["items"]] == [pid]
    assert (await get_event(client, admin, pid))["genders"] == TWO


@pytest.mark.requirement("eligibility:R34")
@pytest.mark.asyncio
async def test_should_refuse_group_writes_when_they_send_gender(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    group = await client.post(
        "/v1/groups", json={"name": "Girls", "genders": TWO}, headers=auth(admin)
    )
    assert group.status_code == 201, group.text
    gid = group.json()["id"]

    created = await client.post(
        "/v1/groups", json={"name": "Boys", "gender": "male"}, headers=auth(admin)
    )
    updated = await client.patch(
        f"/v1/groups/by_id/{gid}", json={"gender": None}, headers=auth(admin)
    )

    for response in (created, updated):
        assert _refuses_gender(response), response.text
    detail = await client.get(f"/v1/groups/by_id/{gid}", headers=auth(admin))
    assert detail.json()["genders"] == TWO
    listed = await client.get("/v1/groups", headers=auth(admin))
    assert [g["name"] for g in listed.json()["items"]] == ["Girls"]


@pytest.mark.requirement("eligibility:R34")
@pytest.mark.asyncio
async def test_should_send_genders_only_when_event_is_read(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    one = await create_oneoff(
        client, admin, venue, genders=["female"], visibility="public"
    )

    reads = [
        one,
        await get_event(client, admin, one["id"]),
        (await client.get("/v1/events", headers=auth(admin))).json()["items"][0],
    ]
    public = await client.get(
        f"/v1/public/events/{generate_event_public_id(one['id'])}"
    )
    assert public.status_code == 200, public.text
    reads.append(public.json())

    for body in reads:
        assert body["genders"] == ["female"]
        assert "gender" not in body


@pytest.mark.requirement("eligibility:R34")
@pytest.mark.asyncio
async def test_should_send_genders_only_when_group_is_read(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await client.post(
        "/v1/groups", json={"name": "Girls", "genders": ["female"]}, headers=auth(admin)
    )
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    updated = await client.patch(
        f"/v1/groups/by_id/{gid}", json={"name": "Girls U16"}, headers=auth(admin)
    )
    assert updated.status_code == 200, updated.text

    reads = [
        created.json(),
        updated.json(),
        (await client.get(f"/v1/groups/by_id/{gid}", headers=auth(admin))).json(),
        (await client.get("/v1/groups", headers=auth(admin))).json()["items"][0],
    ]

    for body in reads:
        assert body["genders"] == ["female"]
        assert "gender" not in body
