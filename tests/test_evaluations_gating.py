"""Evaluations are optional per deployment (#302, R61)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import settings

from .evaluation_helpers import auth
from .helpers import create_admin_user


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R61")
@pytest.mark.requirement("evaluation:R61a")
async def test_should_refuse_evaluations_when_module_disabled(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """R61: a deployment that does not ship evaluations refuses every route."""
    monkeypatch.setattr(settings, "evaluations_enabled", False)
    admin_token = await create_admin_user(db_session)

    response = await client.get("/v1/evaluations", headers=auth(admin_token))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EVALUATIONS_DISABLED"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R61a")
async def test_should_refuse_templates_when_module_disabled(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """R61: the template surface is gated by the same seam."""
    monkeypatch.setattr(settings, "evaluations_enabled", False)
    admin_token = await create_admin_user(db_session)

    response = await client.get("/v1/evaluations/templates", headers=auth(admin_token))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EVALUATIONS_DISABLED"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R61")
async def test_should_serve_evaluations_when_module_enabled(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """R61: the same route answers normally once the deployment ships them."""
    monkeypatch.setattr(settings, "evaluations_enabled", True)
    admin_token = await create_admin_user(db_session)

    response = await client.get("/v1/evaluations", headers=auth(admin_token))

    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R61a")
async def test_should_report_evaluations_off_in_capabilities(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """The capabilities object tells a client which modules this deployment has.

    Authenticated rather than public by design (#339) — every consumer is
    post-login.
    """
    monkeypatch.setattr(settings, "evaluations_enabled", False)
    admin_token = await create_admin_user(db_session)

    response = await client.get("/v1/capabilities", headers=auth(admin_token))

    assert response.status_code == 200
    assert response.json()["evaluations"] is False


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R61a")
async def test_should_report_evaluations_on_in_capabilities(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """The same object flips when the module is enabled."""
    monkeypatch.setattr(settings, "evaluations_enabled", True)
    admin_token = await create_admin_user(db_session)

    response = await client.get("/v1/capabilities", headers=auth(admin_token))

    assert response.status_code == 200
    assert response.json()["evaluations"] is True


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R61a")
async def test_should_keep_routes_registered_when_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """R61: the published API must not vary with configuration.

    A disabled module answers 503, it does not vanish — otherwise a
    generated SDK would differ per deployment.
    """
    monkeypatch.setattr(settings, "evaluations_enabled", False)

    schema = await client.get("/openapi.json")

    assert schema.status_code == 200
    assert "/v1/evaluations" in schema.json()["paths"]
