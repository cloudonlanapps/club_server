"""The deployment's default country code (#15).

The club apps store phone numbers in international format; a number typed
without a country code takes the club's own, and which country a club is in
is a fact about the deployment. ``DEFAULT_COUNTRY_CODE`` carries it and
``GET /v1/capabilities`` reports it, to anonymous callers too, because the
sign-up and public inquiry forms need it before anyone logs in.
"""

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.config import Settings, settings
from club_server.schemas.common import CapabilitiesResponse

from .credit_helpers import auth, create_member

CAPABILITIES = "/v1/capabilities"
SETTING = "DEFAULT_COUNTRY_CODE"


@pytest.mark.requirement("platform:R14b")
@pytest.mark.asyncio
async def test_should_report_the_country_code_when_the_deployment_sets_one(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Signed in or not, the code comes back as the string it was set to."""
    monkeypatch.setattr(settings, "default_country_code", "91")
    token = await create_member(db_session, "alice")

    anonymous = await client.get(CAPABILITIES)
    signed_in = await client.get(CAPABILITIES, headers=auth(token))

    assert anonymous.status_code == 200
    assert anonymous.json()["defaultCountryCode"] == "91"
    assert signed_in.status_code == 200
    assert signed_in.json()["defaultCountryCode"] == "91"


@pytest.mark.requirement("platform:R14b")
@pytest.mark.asyncio
async def test_should_report_null_when_the_deployment_sets_no_country_code(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Unset is the default, and it is reported as null rather than left out."""
    monkeypatch.setattr(settings, "default_country_code", None)

    response = await client.get(CAPABILITIES)

    assert response.status_code == 200
    assert "defaultCountryCode" in response.json()
    assert response.json()["defaultCountryCode"] is None


@pytest.mark.requirement("platform:R14b")
@pytest.mark.parametrize("code", ["1", "91", "971"])
def test_should_read_the_country_code_from_the_environment_when_one_to_three_digits(
    monkeypatch: pytest.MonkeyPatch, code: str
):
    monkeypatch.setenv(SETTING, code)

    assert Settings().default_country_code == code


@pytest.mark.requirement("platform:R14b")
def test_should_default_to_no_country_code_when_the_setting_is_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(SETTING, raising=False)

    assert Settings().default_country_code is None


@pytest.mark.requirement("platform:R14c")
@pytest.mark.parametrize("code", ["+91", "1234", "9a", "9 1", "-1", "९१"])
def test_should_refuse_to_start_when_the_country_code_is_not_one_to_three_digits(
    monkeypatch: pytest.MonkeyPatch, code: str
):
    """The failure names the setting, so the operator knows which line to fix."""
    monkeypatch.setenv(SETTING, code)

    with pytest.raises(ValidationError) as failure:
        _ = Settings()

    assert "default_country_code" in str(failure.value)


@pytest.mark.requirement("platform:R14c")
@pytest.mark.parametrize("blank", ["", "   "])
def test_should_treat_an_empty_country_code_as_not_set(
    monkeypatch: pytest.MonkeyPatch, blank: str
):
    """A deploy conf writes a key it has no value for as an empty string."""
    monkeypatch.setenv(SETTING, blank)

    assert Settings().default_country_code is None


@pytest.mark.requirement("platform:R14b")
def test_should_round_trip_the_country_code_through_the_capabilities_schema():
    document = CapabilitiesResponse(credit_system=False, default_country_code="91")

    dumped = document.model_dump(by_alias=True)

    assert dumped["defaultCountryCode"] == "91"
    assert CapabilitiesResponse.model_validate(dumped) == document
    assert CapabilitiesResponse(credit_system=False).default_country_code is None


@pytest.mark.requirement("platform:R14b")
@pytest.mark.asyncio
async def test_should_describe_the_country_code_field_in_openapi(client: AsyncClient):
    schema = (await client.get("/openapi.json")).json()

    field = schema["components"]["schemas"]["CapabilitiesResponse"]["properties"][
        "defaultCountryCode"
    ]

    assert "country calling code" in field["description"]
    assert "DEFAULT_COUNTRY_CODE" in field["description"]
