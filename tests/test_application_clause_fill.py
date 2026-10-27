"""Filling one library clause for the editor (#107, applications R7, R9a).

The clause library is an auto-fill source: the editor sends one clause and
the facts for its gaps, and inserts the text it gets back as custom text.
The endpoint reads and writes no template.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .application_helpers import (
    APPLICATIONS,
    auth,
    create_applicant,
    create_application,
    day,
    get_application,
)
from .helpers import create_admin_user, create_coach_user

pytestmark = pytest.mark.usefixtures("applications_enabled")

FILL = f"{APPLICATIONS}/clauses/fill"
MATCHES = {"type": "date_range", "from": day(2026, 11, 19), "to": day(2026, 11, 21)}
VENUE = {"type": "name", "value": {"en": "City Rink", "mr": "सिटी रिंक"}}


async def fill(
    client: AsyncClient, token: str, clause: str, facts: dict[str, Any]
) -> Response:
    return await client.post(
        FILL, json={"clause": clause, "facts": facts}, headers=auth(token)
    )


@pytest.mark.requirement("applications:R7")
@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
async def test_should_fill_a_rule_in_every_language_when_admin_gives_its_facts(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await fill(
        client, admin, "rule.matches", {"dates": MATCHES, "venue": VENUE}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clause"] == "rule.matches"
    assert set(body["text"]) == set(body["heading"]) == {"en", "mr", "hi"}
    assert body["heading"]["en"] == "Matches"  # numbered only when printed
    assert body["text"]["en"] == (
        "Tournament matches will take place from 19–21 November 2026 at City Rink."
    )
    assert body["text"]["mr"] == (
        "19 ते 21 नोव्हेंबर 2026 दरम्यान सिटी रिंक येथे स्पर्धेचे सामने होतील."
    )
    assert "City Rink" in body["text"]["hi"]  # a name without Hindi: English


@pytest.mark.requirement("applications:R7")
@pytest.mark.asyncio
async def test_should_group_an_amount_the_indian_way_when_a_fee_is_filled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await fill(
        client, admin, "rule.fee", {"amount": {"type": "amount", "value": 150000}}
    )

    assert response.status_code == 200, response.text
    texts = response.json()["text"]
    assert all("1,50,000" in texts[lang] for lang in ("en", "mr", "hi"))
    assert texts["en"] == "A registration fee of ₹1,50,000 is payable at registration."


@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
async def test_should_fill_a_clause_without_gaps_or_heading_when_it_has_none(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await fill(client, admin, "declaration.trial", {})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["heading"] is None
    assert body["text"]["en"].startswith("I hereby declare")


@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
async def test_should_leave_every_template_unchanged_when_a_clause_is_filled(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)

    response = await fill(
        client, admin, "rule.matches", {"dates": MATCHES, "venue": VENUE}
    )

    assert response.status_code == 200, response.text
    read = await get_application(client, admin, created["id"])
    assert read.json() == created


@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clause", "facts", "reason"),
    [
        ("declaration.unknown", {}, "no clause 'declaration.unknown'"),
        ("rule.matches", {"dates": MATCHES}, "needs facts for venue"),
        (
            "selection.attend_training_camp",
            {"dates": {"type": "name", "value": "98765 43210"}},
            "gap 'dates' needs a date_range, not a name",
        ),
        (
            "rule.fee",
            {"amount": {"type": "amount", "value": 5}, "venue": VENUE},
            "has no gap 'venue'",
        ),
        (
            "header.matches",
            {"dates": {"type": "choices", "value": ["skis"]}},
            "gap 'dates' needs a date_range, not a choices",
        ),
    ],
)
async def test_should_refuse_a_fill_when_the_clause_or_its_facts_do_not_fit(
    client: AsyncClient,
    db_session: AsyncSession,
    clause: str,
    facts: dict[str, Any],
    reason: str,
):
    admin = await create_admin_user(db_session)

    response = await fill(client, admin, clause, facts)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "APPLICATION_INVALID_REFERENCE"
    assert reason in detail["message"]


@pytest.mark.requirement("applications:R7")
@pytest.mark.asyncio
async def test_should_refuse_a_fill_when_a_choice_is_not_in_the_library(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    response = await fill(
        client,
        admin,
        "selection.attend_practice",
        {"dates": MATCHES, "kinds": {"type": "choices", "value": ["skis"]}},
    )

    assert response.status_code == 422, response.text
    assert "no choice 'skis'" in response.json()["detail"]["message"]


@pytest.mark.requirement("applications:R9a")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member"])
async def test_should_refuse_a_fill_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    token = (
        await create_coach_user(db_session)
        if role == "coach"
        else await create_applicant(db_session, "ravi")
    )

    response = await fill(client, token, "declaration.trial", {})

    assert response.status_code == 403
