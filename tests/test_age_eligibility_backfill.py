"""The migration that turns stored date bounds into age bands (#16, R30).

A database built from the models has no date columns, so these tests add
them the way they were, store bounds in them, and run the backfill the
migration runs.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_day, club_today
from club_server.db.backfills.age_eligibility import (
    convert_dob_bounds_to_age_bands,
    restore_dob_bounds_from_age_bands,
)

from .eligibility_helpers import day
from .helpers import create_admin_user
from .redesign_helpers import (
    DAY_MS,
    EVERY_DAY,
    at,
    auth,
    cancel_occurrence,
    create_camp,
    create_oneoff,
    create_programme,
    create_venue,
    get_event,
)

AFTER = day(2010, 1, 31)
BEFORE = day(2014, 3, 30)


async def store_old_bounds(
    db: AsyncSession, table: str, row_id: int, after: int | None, before: int | None
) -> None:
    """Put the retired date columns back and store ``after`` / ``before``."""
    for column in ("dob_on_or_after_utc", "dob_on_or_before_utc"):
        _ = await db.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} BIGINT")
        )
    _ = await db.execute(
        text(
            f"UPDATE {table} SET dob_on_or_after_utc = :after, "
            "dob_on_or_before_utc = :before WHERE id = :id"
        ),
        {"after": after, "before": before, "id": row_id},
    )
    await db.commit()


async def run_backfill(db: AsyncSession) -> dict[str, int]:
    for table in ("events", "groups"):
        for column in ("dob_on_or_after_utc", "dob_on_or_before_utc"):
            _ = await db.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} BIGINT")
            )
    raw = await db.connection()
    counts = await raw.run_sync(convert_dob_bounds_to_age_bands)
    await db.commit()
    db.expire_all()
    return counts


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R30")
async def test_should_report_the_dates_a_group_stored_before_the_migration(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await client.post(
        "/v1/groups", json={"name": "U16", "gender": "male"}, headers=auth(admin)
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]
    await store_old_bounds(db_session, "groups", group_id, AFTER, BEFORE)

    assert (await run_backfill(db_session))["groups"] == 1

    fetched = await client.get(f"/v1/groups/by_id/{group_id}", headers=auth(admin))
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["dobOnOrAfterUtc"] == AFTER
    assert body["dobOnOrBeforeUtc"] == BEFORE
    assert body["strictAge"] is True
    assert body["eligibilityReferenceDayUtc"] == club_today()


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R30")
@pytest.mark.parametrize("kind", ["camp", "oneOff"])
async def test_should_report_the_dates_a_camp_or_one_off_stored_before_the_migration(
    client: AsyncClient, db_session: AsyncSession, kind: str
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=40)
    create = create_camp if kind == "camp" else create_oneoff
    event = await create(client, admin, venue, start=start)
    await store_old_bounds(db_session, "events", event["id"], AFTER, BEFORE)

    assert (await run_backfill(db_session))["events"] == 1

    fetched = await get_event(client, admin, event["id"])
    assert fetched["dobOnOrAfterUtc"] == AFTER
    assert fetched["dobOnOrBeforeUtc"] == BEFORE
    assert fetched["strictAge"] is True
    assert fetched["eligibilityReferenceDayUtc"] == club_day(start)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R30")
async def test_should_report_the_dates_a_programme_stored_before_the_migration(
    client: AsyncClient, db_session: AsyncSession
):
    """Counted from the next live occurrence: the first one is cancelled."""
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    start = at(days=10)
    programme = await create_programme(
        client, admin, venue, start=start, rrule=EVERY_DAY
    )
    cancelled = await cancel_occurrence(client, admin, programme["id"], start)
    assert cancelled.status_code in (200, 204), cancelled.text
    await store_old_bounds(db_session, "events", programme["id"], AFTER, None)

    assert (await run_backfill(db_session))["events"] == 1

    fetched = await get_event(client, admin, programme["id"])
    assert fetched["dobOnOrAfterUtc"] == AFTER
    assert fetched["dobOnOrBeforeUtc"] is None
    assert fetched["minAge"] is None
    assert fetched["eligibilityReferenceDayUtc"] == club_day(start + DAY_MS)


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R30")
async def test_should_leave_records_without_a_date_bound_untouched(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event = await create_oneoff(client, admin, venue)

    counts = await run_backfill(db_session)

    assert counts == {"groups": 0, "events": 0}
    fetched = await get_event(client, admin, event["id"])
    assert fetched["minAge"] is None
    assert fetched["maxAge"] is None
    assert fetched["strictAge"] is False


@pytest.mark.asyncio
@pytest.mark.requirement("eligibility:R30")
async def test_should_put_the_dates_back_when_the_migration_is_reversed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    venue = await create_venue(client, admin)
    event = await create_camp(client, admin, venue, start=at(days=40))
    await store_old_bounds(db_session, "events", event["id"], AFTER, BEFORE)
    _ = await run_backfill(db_session)
    await store_old_bounds(db_session, "events", event["id"], None, None)

    raw = await db_session.connection()
    counts = await raw.run_sync(restore_dob_bounds_from_age_bands)
    await db_session.commit()

    assert counts["events"] == 1
    stored = (
        await db_session.execute(
            text(
                "SELECT dob_on_or_after_utc, dob_on_or_before_utc FROM events "
                "WHERE id = :id"
            ),
            {"id": event["id"]},
        )
    ).one()
    assert tuple(stored) == (AFTER, BEFORE)
