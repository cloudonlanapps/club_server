"""The retention row seeded by the table-creation migration reads as never
written once it is dropped (#518, platform:R3a).

A database built from the models has no seeded row, so these tests insert
it the way migration ``w7x8y9z0a1b2`` did, then run the backfill that the
new migration runs.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.backfills.retention_seed import drop_seeded_retention_row

from .helpers import create_admin_user

PREFS = "/v1/admin/preferences"
RETENTION = "notification_info_retention_days"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_as_old_migration(db: AsyncSession, value: str = "90") -> None:
    await db.execute(
        text(
            "INSERT INTO system_preferences (key, value, updated_at, updated_by) "
            "VALUES (:key, CAST(:value AS jsonb), "
            "(EXTRACT(EPOCH FROM NOW()) * 1000)::bigint, NULL)"
        ),
        {"key": RETENTION, "value": value},
    )
    await db.commit()


async def _run_backfill(db: AsyncSession) -> int:
    raw = await db.connection()
    deleted = await raw.run_sync(drop_seeded_retention_row)
    await db.commit()
    return deleted


@pytest.mark.requirement("platform:R3a")
@pytest.mark.asyncio
async def test_should_read_default_without_update_time_when_seeded_row_is_dropped(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _seed_as_old_migration(db_session)
    seeded = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))
    assert seeded.status_code == 200, seeded.text
    assert isinstance(seeded.json()["updatedAtUtc"], int)

    assert await _run_backfill(db_session) == 1

    response = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "key": RETENTION,
        "value": 90,
        "updatedAtUtc": None,
        "updatedBy": None,
    }
    assert await _run_backfill(db_session) == 0


@pytest.mark.asyncio
async def test_should_keep_preference_when_a_person_wrote_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    written = await client.patch(
        f"{PREFS}/{RETENTION}", json={"value": 90}, headers=auth(admin)
    )
    assert written.status_code == 200, written.text

    assert await _run_backfill(db_session) == 0

    response = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))
    assert response.status_code == 200, response.text
    assert response.json()["value"] == 90
    assert response.json()["updatedBy"] == "admin"
    assert isinstance(response.json()["updatedAtUtc"], int)


@pytest.mark.asyncio
async def test_should_keep_preference_when_value_differs_from_the_seed(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await _seed_as_old_migration(db_session, value="30")

    assert await _run_backfill(db_session) == 0

    response = await client.get(f"{PREFS}/{RETENTION}", headers=auth(admin))
    assert response.status_code == 200, response.text
    assert response.json()["value"] == 30
    assert isinstance(response.json()["updatedAtUtc"], int)
