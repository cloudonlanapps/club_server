"""The built-in clauses seeded into the club's library once (#133, applications R7b).

The first use of the library copies every clause of
``assets/applications/clauses.json`` into the club's texts, keeping its key,
and records that it did: from then on the seeded texts are the club's own,
edited and deleted like any other, and nothing is seeded again.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.application_library_text import ApplicationLibraryText
from club_server.services.application_catalogue import library
from club_server.services.application_seed import (
    ensure_library_seeded,
    seeded_name,
)

from .application_helpers import APPLICATIONS, auth
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

LIBRARY = f"{APPLICATIONS}/library"
CLAUSES = f"{APPLICATIONS}/clauses"


def builtin_clauses() -> dict[str, dict[str, Any]]:
    clauses: dict[str, dict[str, Any]] = library()["clauses"]
    return clauses


async def listed(client: AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get(LIBRARY, headers=auth(token))
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()
    return items


async def text_count(db: AsyncSession) -> int:
    count = await db.scalar(select(func.count()).select_from(ApplicationLibraryText))
    return int(count or 0)


@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_hold_every_builtin_clause_when_the_library_is_first_read(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    texts = await listed(client, admin)

    by_key = {t["key"]: t for t in texts}
    assert list(by_key) == list(builtin_clauses())  # the library's order
    for key, clause in builtin_clauses().items():
        seeded = by_key[key]
        assert isinstance(seeded["id"], int)
        assert seeded["kind"] == clause["kind"]
        assert seeded["heading"] == clause.get("heading")
        assert seeded["text"] == clause["text"]
        assert seeded["name"] == seeded_name(key, clause)
        assert seeded["createdBy"] is None
    assert by_key["rule.matches"]["name"] == "Matches"
    assert by_key["header.training_camp"]["name"] == "Header: training camp"
    assert "_review" not in str(texts)


@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_seed_nothing_when_the_server_starts_again(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    first = await listed(client, admin)

    await ensure_library_seeded(db_session)
    await ensure_library_seeded(db_session)
    await db_session.commit()

    assert await text_count(db_session) == len(first) == len(builtin_clauses())
    assert await listed(client, admin) == first


@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_keep_a_seeded_text_deleted_when_the_server_starts_again(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    seeded = next(t for t in await listed(client, admin) if t["key"] == "rule.fee")

    deleted = await client.delete(f"{LIBRARY}/{seeded['id']}", headers=auth(admin))
    await ensure_library_seeded(db_session)
    await db_session.commit()

    assert deleted.status_code == 204, deleted.text
    keys = [t["key"] for t in await listed(client, admin)]
    assert "rule.fee" not in keys
    assert len(keys) == len(builtin_clauses()) - 1


@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_keep_a_seeded_text_edited_when_the_server_starts_again(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    seeded = next(t for t in await listed(client, admin) if t["key"] == "rule.matches")
    text = {"en": "Matches run from {dates} at {venue}.", "mr": "सामने {dates}."}

    edited = await client.patch(
        f"{LIBRARY}/{seeded['id']}",
        json={"name": "Our matches", "text": text},
        headers=auth(admin),
    )
    await ensure_library_seeded(db_session)
    await db_session.commit()

    assert edited.status_code == 200, edited.text
    assert edited.json()["key"] == "rule.matches"
    again = next(t for t in await listed(client, admin) if t["key"] == "rule.matches")
    assert again["id"] == seeded["id"]
    assert again["name"] == "Our matches"
    assert again["text"] == text
    assert again["heading"] == seeded["heading"]


@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_seed_once_when_a_club_text_is_written_first(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = {"kind": "note", "name": "Kit", "text": {"en": "Bring a helmet."}}

    created = await client.post(LIBRARY, json=body, headers=auth(admin))

    assert created.status_code == 201, created.text
    assert created.json()["key"] is None
    texts = await listed(client, admin)
    assert len(texts) == len(builtin_clauses()) + 1
    assert texts[-1]["id"] == created.json()["id"]  # seeded before it


@pytest.mark.requirement("applications:R7")
@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_list_the_library_texts_as_the_clauses(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = {"kind": "note", "name": "Kit", "text": {"en": "Bring a helmet."}}
    _ = await client.post(LIBRARY, json=body, headers=auth(admin))
    seeded = next(
        t for t in await listed(client, admin) if t["key"] == "note.follow_rules"
    )
    _ = await client.delete(f"{LIBRARY}/{seeded['id']}", headers=auth(admin))

    response = await client.get(CLAUSES, headers=auth(admin))

    assert response.status_code == 200, response.text
    body_json = response.json()
    assert body_json["clauses"] == await listed(client, admin)
    assert "note.follow_rules" not in [c["key"] for c in body_json["clauses"]]
    assert all("source" not in c and "gaps" not in c for c in body_json["clauses"])
    assert body_json["choices"]["on_ice"]["en"] == "on-ice"


@pytest.mark.requirement("applications:R7b")
@pytest.mark.asyncio
async def test_should_update_and_delete_every_seeded_text(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    for seeded in await listed(client, admin):
        edited = await client.patch(
            f"{LIBRARY}/{seeded['id']}",
            json={"name": f"Ours {seeded['id']}"},
            headers=auth(admin),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["name"] == f"Ours {seeded['id']}"
        assert edited.json()["text"] == seeded["text"]
        deleted = await client.delete(f"{LIBRARY}/{seeded['id']}", headers=auth(admin))
        assert deleted.status_code == 204, deleted.text

    assert await listed(client, admin) == []
    clauses = await client.get(CLAUSES, headers=auth(admin))
    assert clauses.json()["clauses"] == []
