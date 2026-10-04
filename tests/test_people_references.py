"""People are referenced by username FK everywhere in event management (#386).

The organizer of a schedule and its coaches point at ``users.username``;
a coach assignment is a row of ``event_schedule_coaches`` rather than a
name in a JSON list. No name is stored that is not a user, and the delete
rule is ``CASCADE`` on purpose: ordinary deletion is the soft delete, which
keeps every reference; the super-admin hard delete wipes the person, and
their organizer and coach references go with them.
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.people_references import reconcile_people_references
from club_server.db.models.event_schedule import EventSchedule
from club_server.db.models.event_schedule_coach import EventScheduleCoach

from .helpers import create_admin_user, create_coach_user, create_member_user
from .redesign_helpers import (
    at,
    auth,
    create_camp,
    create_programme,
    create_venue,
    get_event,
    split,
)


async def _schedules(client: AsyncClient, token: str, event_id: int) -> list[dict]:
    response = await client.get(
        f"/v1/events/by_id/{event_id}/schedules", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _hard_delete(client: AsyncClient, admin: str, username: str) -> None:
    soft = await client.delete(f"/v1/users/by_id/{username}", headers=auth(admin))
    assert soft.status_code == 200, soft.text
    hard = await client.delete(f"/v1/users/by_id/{username}/hard", headers=auth(admin))
    assert hard.status_code == 204, hard.text


# --- creating and reading -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("programme:R20a")
async def test_should_reject_create_when_a_coach_is_not_a_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    venue = await create_venue(client, admin)
    await db_session.commit()

    response = await client.post(
        "/v1/events",
        json={
            "title": "Skills",
            "type": "camp",
            "venueId": venue,
            "startTimeUtc": at(days=2),
            "endTimeUtc": at(days=2, hours=1),
            "rrule": "FREQ=DAILY;COUNT=3",
            "coachNames": ["ann", "nobody"],
        },
        headers=auth(admin),
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"
    assert "nobody" in response.json()["detail"]["message"]

    listed = await client.get("/v1/events", headers=auth(admin))
    assert listed.status_code == 200
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("programme:R20a")
async def test_should_store_coaches_in_order_when_they_are_users(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)

    event = await create_camp(client, admin, venue, coachNames=["bob", "ann"])
    assert event["coachNames"] == ["bob", "ann"]

    fetched = await get_event(client, admin, event["id"])
    assert fetched["coachNames"] == ["bob", "ann"]
    schedules = await _schedules(client, admin, event["id"])
    assert [s["coachNames"] for s in schedules] == [["bob", "ann"]]
    rows = (
        await db_session.execute(
            select(EventScheduleCoach.username, EventScheduleCoach.position)
            .join(EventSchedule, EventSchedule.id == EventScheduleCoach.schedule_id)
            .where(EventSchedule.event_id == event["id"])
            .order_by(EventScheduleCoach.position)
        )
    ).all()
    assert [(u, p) for u, p in rows] == [("bob", 0), ("ann", 1)]


@pytest.mark.asyncio
async def test_should_report_no_coaches_when_none_assigned(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)

    event = await create_camp(client, admin, venue)

    assert event["coachNames"] is None
    assert (await get_event(client, admin, event["id"]))["coachNames"] is None


# --- changing ----------------------------------------------------------


@pytest.mark.asyncio
async def test_should_replace_coaches_when_camp_is_updated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    for name in ("ann", "bob", "cid"):
        _ = await create_coach_user(db_session, name)
    venue = await create_venue(client, admin)
    event = await create_camp(client, admin, venue, coachNames=["ann", "bob"])

    updated = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"coachNames": ["cid"], "version": 1},
        headers=auth(admin),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["coachNames"] == ["cid"]

    assert (await get_event(client, admin, event["id"]))["coachNames"] == ["cid"]
    remaining = (
        (
            await db_session.execute(
                select(EventScheduleCoach.username)
                .join(EventSchedule, EventSchedule.id == EventScheduleCoach.schedule_id)
                .where(EventSchedule.event_id == event["id"])
            )
        )
        .scalars()
        .all()
    )
    assert list(remaining) == ["cid"]


@pytest.mark.asyncio
@pytest.mark.requirement("programme:R20a")
async def test_should_reject_update_when_a_coach_is_not_a_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    venue = await create_venue(client, admin)
    event = await create_camp(client, admin, venue, coachNames=["ann"])

    updated = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"coachNames": ["ghost"], "version": 1},
        headers=auth(admin),
    )
    assert updated.status_code == 404, updated.text
    assert updated.json()["detail"]["code"] == "USER_NOT_FOUND"

    assert (await get_event(client, admin, event["id"]))["coachNames"] == ["ann"]


@pytest.mark.asyncio
async def test_should_clear_coaches_when_update_sends_empty_list(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    venue = await create_venue(client, admin)
    event = await create_camp(client, admin, venue, coachNames=["ann"])

    updated = await client.patch(
        f"/v1/events/by_id/{event['id']}",
        json={"coachNames": [], "version": 1},
        headers=auth(admin),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["coachNames"] is None
    assert (await get_event(client, admin, event["id"]))["coachNames"] is None


@pytest.mark.asyncio
async def test_should_coach_only_the_new_schedule_when_programme_is_split(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    start = at(days=2)
    event = await create_programme(
        client, admin, venue, start=start, coachNames=["ann"]
    )
    cutoff = start + 24 * 60 * 60 * 1000

    response = await split(
        client, admin, event["id"], effectiveDateTimeUtc=cutoff, coachNames=["bob"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["coachNames"] == ["bob"]

    schedules = await _schedules(client, admin, event["id"])
    assert [s["coachNames"] for s in schedules] == [["ann"], ["bob"]]


@pytest.mark.asyncio
@pytest.mark.requirement("programme:R20a")
async def test_should_reject_split_when_a_coach_is_not_a_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    venue = await create_venue(client, admin)
    start = at(days=2)
    event = await create_programme(
        client, admin, venue, start=start, coachNames=["ann"]
    )

    response = await split(
        client,
        admin,
        event["id"],
        effectiveDateTimeUtc=start + 24 * 60 * 60 * 1000,
        coachNames=["ghost"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    schedules = await _schedules(client, admin, event["id"])
    assert [s["coachNames"] for s in schedules] == [["ann"]]


# --- deletion ----------------------------------------------------------


@pytest.mark.asyncio
async def test_should_keep_references_when_user_is_soft_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    event = await create_camp(
        client, admin, venue, organizerName="ann", coachNames=["ann", "bob"]
    )

    soft = await client.delete("/v1/users/by_id/ann", headers=auth(admin))
    assert soft.status_code == 200, soft.text

    fetched = await get_event(client, admin, event["id"])
    assert fetched["organizerName"] == "ann"
    assert fetched["coachNames"] == ["ann", "bob"]


@pytest.mark.requirement("users:R54")
@pytest.mark.asyncio
@pytest.mark.requirement("programme:R20a")
async def test_should_pass_events_to_deleting_super_admin_when_organizer_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """The events an organizer ran are not orphaned: the super admin who
    hard-deletes them takes them over. Their coach rows simply go."""
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_coach_user(db_session, "bob")
    venue = await create_venue(client, admin)
    event = await create_camp(
        client, admin, venue, organizerName="ann", coachNames=["ann", "bob"]
    )
    other = await create_camp(
        client, admin, venue, start=at(days=9), organizerName="bob"
    )

    await _hard_delete(client, admin, "ann")

    fetched = await get_event(client, admin, event["id"])
    assert fetched["organizerName"] == "admin"
    assert fetched["coachNames"] == ["bob"]
    schedules = await _schedules(client, admin, event["id"])
    assert [s["organizerName"] for s in schedules] == ["admin"]
    assert [s["coachNames"] for s in schedules] == [["bob"]]
    assert (await get_event(client, admin, other["id"]))["organizerName"] == "bob"


# --- the migration's reconciliation ----------------------------------------


async def _legacy_shape(db: AsyncSession) -> None:
    """Put the tables back the way the migration finds them: the JSON coach
    column present, the FKs absent, so rows naming non-users can exist."""
    await db.execute(text("ALTER TABLE event_schedules ADD COLUMN coach_names text"))
    await db.execute(
        text("ALTER TABLE event_schedules DROP CONSTRAINT fk_event_schedules_organizer")
    )
    await db.execute(
        text(
            "ALTER TABLE occurrence_overrides "
            "DROP CONSTRAINT fk_occurrence_overrides_new_organizer"
        )
    )


async def _run(db: AsyncSession) -> dict[str, int]:
    raw = await db.connection()
    return await raw.run_sync(reconcile_people_references)


@pytest.mark.asyncio
async def test_should_reconcile_legacy_people_references_when_migrating(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    _ = await create_member_user(db_session, "bob")
    venue = await create_venue(client, admin)
    start = at(days=2)
    kept = await create_camp(client, admin, venue, start=start)
    orphaned = await create_camp(client, admin, venue, start=at(days=9))
    await db_session.commit()
    await _legacy_shape(db_session)
    await db_session.execute(
        text(
            "UPDATE event_schedules SET organizer_name = 'ann', coach_names = :c "
            "WHERE event_id = :e"
        ),
        {"c": json.dumps(["bob", "nobody", "ann"]), "e": kept["id"]},
    )
    await db_session.execute(
        text(
            "UPDATE event_schedules SET organizer_name = 'vanished', "
            "coach_names = :c WHERE event_id = :e"
        ),
        {"c": json.dumps(["gone"]), "e": orphaned["id"]},
    )
    await db_session.execute(
        text(
            "INSERT INTO occurrence_overrides "
            "(event_id, occurrence_time, status, new_organizer_name) "
            "VALUES (:e, :t, 'rescheduled', 'vanished')"
        ),
        {"e": kept["id"], "t": start},
    )
    await db_session.flush()

    counts = await _run(db_session)

    assert counts == {
        "organizers_cleared": 1,
        "override_organizers_cleared": 1,
        "coaches_linked": 2,
        "coaches_dropped": 2,
    }
    organizers = (
        await db_session.execute(
            text(
                "SELECT event_id, organizer_name FROM event_schedules ORDER BY event_id"
            )
        )
    ).all()
    assert [(e, o) for e, o in organizers] == [
        (kept["id"], "ann"),
        (orphaned["id"], None),
    ]
    coaches = (
        await db_session.execute(
            text(
                "SELECT s.event_id, c.username, c.position "
                "FROM event_schedule_coaches c "
                "JOIN event_schedules s ON s.id = c.schedule_id "
                "ORDER BY s.event_id, c.position"
            )
        )
    ).all()
    assert [(e, u, p) for e, u, p in coaches] == [
        (kept["id"], "bob", 0),
        (kept["id"], "ann", 1),
    ]
    override = (
        await db_session.execute(
            text("SELECT new_organizer_name FROM occurrence_overrides")
        )
    ).scalar_one()
    assert override is None

    again = await _run(db_session)
    assert again == {
        "organizers_cleared": 0,
        "override_organizers_cleared": 0,
        "coaches_linked": 0,
        "coaches_dropped": 2,
    }


@pytest.mark.asyncio
async def test_should_change_nothing_when_every_reference_is_a_user(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_coach_user(db_session, "ann")
    venue = await create_venue(client, admin)
    _ = await create_camp(client, admin, venue, organizerName="ann", coachNames=["ann"])
    await db_session.commit()
    await _legacy_shape(db_session)

    counts = await _run(db_session)

    assert counts == {
        "organizers_cleared": 0,
        "override_organizers_cleared": 0,
        "coaches_linked": 0,
        "coaches_dropped": 0,
    }
    coaches = (
        (await db_session.execute(text("SELECT username FROM event_schedule_coaches")))
        .scalars()
        .all()
    )
    assert list(coaches) == ["ann"]
