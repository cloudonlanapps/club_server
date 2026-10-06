"""Server capability discovery (#339).

A client cannot tell from ``openapi.json`` whether a deployment runs the
credit system, because #294 deliberately registers every credit endpoint
on every deployment so the published schema does not vary with
configuration. This endpoint is how it finds out.

Public (#443): the signup page runs before login and needs to know whether
identity verification is on. Nothing here is private: each flag is already
discoverable without logging in (a disabled module's routes answer 503 before
authentication; register answers ``registered`` or ``pending``). A capability
that must stay private cannot join this document.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_member
from .helpers import create_admin_user, create_registered_user

CAPABILITIES = "/v1/capabilities"


@pytest.mark.requirement("platform:R14")
@pytest.mark.asyncio
async def test_should_report_credit_system_off_when_not_enabled(
    client: AsyncClient, db_session: AsyncSession
):
    """The default deployment runs without credit, and says so."""
    token = await create_member(db_session, "alice")

    response = await client.get(CAPABILITIES, headers=auth(token))

    assert response.status_code == 200
    assert response.json()["creditSystem"] is False


@pytest.mark.requirement("platform:R14")
@pytest.mark.asyncio
async def test_should_report_credit_system_on_when_enabled(
    client: AsyncClient, db_session: AsyncSession, credit_enabled: None
):
    """A deployment running on credits reports it."""
    token = await create_member(db_session, "alice")

    response = await client.get(CAPABILITIES, headers=auth(token))

    assert response.status_code == 200
    assert response.json()["creditSystem"] is True


@pytest.mark.requirement("platform:R12")
@pytest.mark.asyncio
async def test_should_answer_an_anonymous_caller_as_a_signed_in_one(
    client: AsyncClient, db_session: AsyncSession
):
    """The signup page reads this before anyone has logged in (#443)."""
    token = await create_member(db_session, "alice")

    anonymous = await client.get(CAPABILITIES)
    signed_in = await client.get(CAPABILITIES, headers=auth(token))

    assert anonymous.status_code == 200
    assert anonymous.json() == signed_in.json()


@pytest.mark.requirement("platform:R14")
@pytest.mark.asyncio
async def test_should_tell_an_anonymous_caller_whether_identity_is_verified(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The flag the signup page needs, in both states (#443)."""
    from club_server.config import settings

    monkeypatch.setattr(settings, "identity_verification_required", False)
    off = await client.get(CAPABILITIES)
    monkeypatch.setattr(settings, "identity_verification_required", True)
    on = await client.get(CAPABILITIES)

    assert off.json()["identityVerification"] is False
    assert on.json()["identityVerification"] is True


@pytest.mark.requirement("platform:R12")
@pytest.mark.asyncio
async def test_should_answer_a_member_awaiting_approval(
    client: AsyncClient, db_session: AsyncSession
):
    """A registered user's app is already rendering and needs to configure
    itself; nothing in this document is privileged."""
    token = await create_registered_user(db_session, "newbie")

    response = await client.get(CAPABILITIES, headers=auth(token))

    assert response.status_code == 200
    assert "creditSystem" in response.json()


@pytest.mark.requirement("platform:R12")
@pytest.mark.asyncio
async def test_should_answer_the_same_for_every_role(
    client: AsyncClient, db_session: AsyncSession
):
    """Capabilities describe the deployment, not the caller."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")

    as_admin = await client.get(CAPABILITIES, headers=auth(admin_token))
    as_member = await client.get(CAPABILITIES, headers=auth(member_token))

    assert as_admin.status_code == 200
    assert as_member.status_code == 200
    assert as_admin.json() == as_member.json()


@pytest.mark.requirement("platform:R13")
@pytest.mark.asyncio
async def test_should_return_a_closed_set_of_capabilities(
    client: AsyncClient, db_session: AsyncSession
):
    """The document is a closed set, so a client can trust what it reads.

    The set grows as modules become optional — ``evaluations`` joined it in
    #302, ``eventMarketing`` in #410, ``identityVerification`` in #428,
    ``defaultCountryCode`` in #15 — which is the shape #339 designed for. It stays asserted exactly
    so that growth is a deliberate edit rather than a silent one.
    """
    token = await create_member(db_session, "alice")

    response = await client.get(CAPABILITIES, headers=auth(token))

    assert set(response.json()) == {
        "creditSystem",
        "evaluations",
        "eventMarketing",
        "identityVerification",
        "defaultCountryCode",
    }
