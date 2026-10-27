"""The club's library of texts, and the clauses list (#126, applications R7, R7a).

A club keeps its own texts beside the built-in clauses: each has a kind (one
of the built-in clauses' kinds), a short name and its text per language,
gaps allowed. They are club-wide: one library for every template. Admins
write and read them; ``GET /v1/applications/clauses`` lists the built-in
clauses and the club's texts together, each marked with its source.
"""

from typing import Any, get_args

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.schemas.application_library import LibraryTextKind

from .application_helpers import APPLICATIONS, auth, create_applicant
from .helpers import create_admin_user, create_coach_user, create_regular_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

LIBRARY = f"{APPLICATIONS}/library"
CLAUSES = f"{APPLICATIONS}/clauses"


def text_body(**changes: Any) -> dict[str, Any]:
    """A club text, as an admin would send it."""
    body: dict[str, Any] = {
        "kind": "selection",
        "name": "Attend the matches",
        "text": {
            "en": "I will play the matches on {match_dates} at {venue}.",
            "mr": "मी {match_dates} रोजी {venue} येथे सामने खेळेन.",
        },
    }
    body.update(changes)
    return body


async def create_text(
    client: AsyncClient, token: str, body: dict[str, Any] | None = None
) -> Response:
    return await client.post(
        LIBRARY, json=text_body() if body is None else body, headers=auth(token)
    )


async def listed(client: AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get(LIBRARY, headers=auth(token))
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()
    return items


async def non_admin(db_session: AsyncSession, role: str) -> str:
    if role == "coach":
        return await create_coach_user(db_session)
    return await create_applicant(db_session, "ravi")


# R7a: writing --------------------------------------------------------------


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_store_a_club_text_with_its_gaps_when_admin_creates_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_regular_admin_user(db_session)

    response = await create_text(client, admin)

    assert response.status_code == 201, response.text
    created = response.json()
    assert isinstance(created["id"], int)
    assert created["kind"] == "selection"
    assert created["name"] == "Attend the matches"
    assert created["text"] == text_body()["text"]  # gaps stored as written
    assert created["heading"] is None
    assert created["createdBy"] == "regular_admin"
    read = await client.get(f"{LIBRARY}/{created['id']}", headers=auth(admin))
    assert read.status_code == 200, read.text
    assert read.json() == created
    assert await listed(client, admin) == [created]


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_store_a_heading_when_a_club_rule_is_created(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = text_body(
        kind="rule",
        name="Practice",
        heading={"en": "Practice", "mr": "सराव"},
        text={"en": "Practice is on {practice_dates}."},
    )

    response = await create_text(client, admin, body)

    assert response.status_code == 201, response.text
    read = await client.get(f"{LIBRARY}/{response.json()['id']}", headers=auth(admin))
    assert read.json()["heading"] == {"en": "Practice", "mr": "सराव"}
    assert read.json()["kind"] == "rule"


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "footer"},  # not a kind of the built-in clauses
        {"name": ""},
        {"text": {}},
        {"text": {"fr": "Bonjour"}},  # not a catalogue language
        {"text": {"en": ""}},
        {"heading": {"en": "Matches"}},  # a heading belongs to a rule only
        {"name": None},
    ],
)
async def test_should_refuse_a_club_text_when_its_shape_does_not_fit(
    client: AsyncClient, db_session: AsyncSession, changes: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await create_text(client, admin, text_body(**changes))

    assert response.status_code == 422, response.text
    assert await listed(client, admin) == []


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_replace_sent_fields_when_admin_edits_a_club_text(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = (await create_text(client, admin)).json()
    new_text = {
        "en": "I will play on {practice_dates}.",
        "mr": "मी {practice_dates} खेळेन.",
    }

    response = await client.patch(
        f"{LIBRARY}/{created['id']}",
        json={"name": "Attend practice", "text": new_text},
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    read = (await client.get(f"{LIBRARY}/{created['id']}", headers=auth(admin))).json()
    assert read["name"] == "Attend practice"
    assert read["text"] == new_text
    assert read["kind"] == "selection"  # left out: kept
    assert read["updatedAtUtc"] >= created["updatedAtUtc"]


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_clear_a_rule_heading_when_edit_sends_null(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    body = text_body(kind="rule", heading={"en": "Kit"}, text={"en": "Helmet."})
    created = (await create_text(client, admin, body)).json()

    response = await client.patch(
        f"{LIBRARY}/{created['id']}", json={"heading": None}, headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    read = (await client.get(f"{LIBRARY}/{created['id']}", headers=auth(admin))).json()
    assert read["heading"] is None


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"kind": None},
        {"name": None},
        {"text": None},
        {"kind": "note", "heading": {"en": "Kit"}},
        {"heading": {"en": "Kit"}},  # the stored text is a selection
        {"kind": "rule-ish"},
    ],
)
async def test_should_refuse_an_edit_when_the_club_text_would_not_fit(
    client: AsyncClient, db_session: AsyncSession, changes: dict[str, Any]
):
    admin = await create_admin_user(db_session)
    created = (await create_text(client, admin)).json()
    await db_session.commit()

    response = await client.patch(
        f"{LIBRARY}/{created['id']}", json=changes, headers=auth(admin)
    )

    assert response.status_code == 422, response.text
    read = (await client.get(f"{LIBRARY}/{created['id']}", headers=auth(admin))).json()
    assert read == created


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_drop_a_club_text_when_admin_deletes_it(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    kept = (await create_text(client, admin, text_body(name="Kept"))).json()
    dropped = (await create_text(client, admin)).json()

    response = await client.delete(f"{LIBRARY}/{dropped['id']}", headers=auth(admin))

    assert response.status_code == 204, response.text
    read = await client.get(f"{LIBRARY}/{dropped['id']}", headers=auth(admin))
    assert read.status_code == 404
    assert read.json()["detail"]["code"] == "APPLICATION_LIBRARY_TEXT_NOT_FOUND"
    assert await listed(client, admin) == [kept]


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "body"), [("GET", None), ("PATCH", {"name": "x"}), ("DELETE", None)]
)
async def test_should_answer_not_found_when_the_club_text_does_not_exist(
    client: AsyncClient, db_session: AsyncSession, method: str, body: Any
):
    admin = await create_admin_user(db_session)

    response = await client.request(
        method, f"{LIBRARY}/999", json=body, headers=auth(admin)
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "APPLICATION_LIBRARY_TEXT_NOT_FOUND"


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member"])
async def test_should_refuse_the_club_library_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    created = (await create_text(client, admin)).json()
    token = await non_admin(db_session, role)
    await db_session.commit()
    path = f"{LIBRARY}/{created['id']}"

    answers = [
        await client.get(LIBRARY, headers=auth(token)),
        await client.get(path, headers=auth(token)),
        await create_text(client, token),
        await client.patch(path, json={"name": "Mine"}, headers=auth(token)),
        await client.delete(path, headers=auth(token)),
    ]

    assert [a.status_code for a in answers] == [403] * 5
    assert await listed(client, admin) == [created]


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_refuse_the_club_library_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    answers = [
        await client.get(LIBRARY),
        await client.post(LIBRARY, json=text_body()),
    ]

    assert [a.status_code for a in answers] == [401, 401]
    assert await listed(client, admin) == []


# R7: the clauses list ------------------------------------------------------


@pytest.mark.requirement("applications:R7")
@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_list_builtin_clauses_and_club_texts_together_when_admin_asks(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    first = (await create_text(client, admin)).json()
    rule = text_body(
        kind="rule", name="Kit", heading={"en": "Kit"}, text={"en": "Helmet."}
    )
    second = (await create_text(client, admin, rule)).json()

    response = await client.get(CLAUSES, headers=auth(admin))

    assert response.status_code == 200, response.text
    clauses = response.json()["clauses"]
    builtin = [c for c in clauses if c["source"] == "builtin"]
    club = [c for c in clauses if c["source"] == "club"]
    assert len(builtin) + len(club) == len(clauses)
    assert {c["kind"] for c in builtin} == set(get_args(LibraryTextKind))
    matches = next(c for c in builtin if c["key"] == "rule.matches")
    assert matches["id"] is None
    assert matches["kind"] == "rule"
    assert matches["gaps"] == {"dates": "date_range", "venue": "name"}
    assert matches["text"]["en"] == (
        "Tournament matches will take place from {dates} at {venue}."
    )
    assert [c["id"] for c in club] == [first["id"], second["id"]]
    assert club[0] == {
        "source": "club",
        "id": first["id"],
        "key": None,
        "kind": "selection",
        "name": "Attend the matches",
        "gaps": None,
        "heading": None,
        "text": first["text"],
    }
    assert club[1]["heading"] == {"en": "Kit"}
    assert clauses.index(club[0]) > clauses.index(builtin[-1])  # built-in first


@pytest.mark.requirement("applications:R7a")
@pytest.mark.asyncio
async def test_should_drop_a_club_text_from_the_clauses_list_when_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = (await create_text(client, admin)).json()
    _ = await client.delete(f"{LIBRARY}/{created['id']}", headers=auth(admin))

    response = await client.get(CLAUSES, headers=auth(admin))

    assert response.status_code == 200, response.text
    assert [c for c in response.json()["clauses"] if c["source"] == "club"] == []
