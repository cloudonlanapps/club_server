"""Unknown request fields must be rejected, not discarded (#325).

Before this, `CamelCaseModel` left Pydantic's default `extra="ignore"` in
place, so a client sending a misspelled or retired field got 200 with the
operation silently not performed — the failure mode that hid a dead
`passwordHash` on user update for as long as it existed.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.schemas.common import CamelCaseModel

from .helpers import create_admin_user


def test_camel_case_model_forbids_undeclared_fields():
    """The base model itself rejects extras, so every schema inherits it."""

    class Sample(CamelCaseModel):
        known_field: str

    assert Sample(knownField="ok").known_field == "ok"

    with pytest.raises(ValueError) as excinfo:
        Sample(knownField="ok", surpriseField="nope")
    assert "surpriseField" in str(excinfo.value)


@pytest.mark.asyncio
async def test_unknown_body_field_returns_422(
    client: AsyncClient, db_session: AsyncSession
):
    """Over HTTP the rejection is a 422 naming the offending field."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/venues",
        json={"name": "Rink", "notAField": "discard me"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(
        err["type"] == "extra_forbidden" and err["loc"][-1] == "notAField"
        for err in detail
    ), detail


@pytest.mark.asyncio
async def test_declared_fields_still_accepted(
    client: AsyncClient, db_session: AsyncSession
):
    """The guard must not reject valid payloads."""
    token = await create_admin_user(db_session)

    response = await client.post(
        "/v1/venues",
        json={"name": "Rink", "address": "1 Ice Road"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Rink"
