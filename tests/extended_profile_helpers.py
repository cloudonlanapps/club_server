"""Shared setup for the extended-profile tests (#92, users R63–R69c)."""

from typing import Any

from httpx import AsyncClient

from club_server.config import settings

PROFILE_OPTIONS = "/v1/system/preferences/profile_options"
ADMIN_PROFILE_OPTIONS = "/v1/admin/preferences/profile_options"
MIDNIGHT_2024_06_01 = 1717200000000  # 2024-06-01 00:00 UTC

OPTIONS: dict[str, Any] = {
    "positions": [
        {"value": "Forward", "label": {"en": "Forward", "mr": "फॉरवर्ड"}},
        {"value": "Goalie", "label": {"en": "Goalie", "hi": "गोलकीपर"}},
    ],
    "languages": ["English", "Marathi"],
}

EXTENDED: dict[str, Any] = {
    "clubDistrict": "Pune District",
    "preferredPositions": ["Forward"],
    "pastExperience": "Two seasons of roller hockey.\nState camp 2024.",
    "nationality": "Indian",
    "preferredLanguages": ["Marathi"],
    "guardian": {"name": "Sunita Rao", "relation": "Mother", "phone": "+919876543210"},
}


def auth(token: str) -> dict[str, str]:
    """Bearer header for ``token``."""
    return {"Authorization": f"Bearer {token}"}


async def write_options(
    client: AsyncClient, admin: str, options: dict[str, Any] | None = None
) -> None:
    """Store the club's profile options as the super admin does (users R69a).

    The write answers 503 while the extended profile is off (R69c), so the
    module is on for this write alone and the test's own setting is kept.
    """
    previous = settings.extended_profile_enabled
    settings.extended_profile_enabled = True
    try:
        response = await client.patch(
            ADMIN_PROFILE_OPTIONS,
            json={"value": OPTIONS if options is None else options},
            headers=auth(admin),
        )
    finally:
        settings.extended_profile_enabled = previous
    assert response.status_code == 200, response.text


async def private(client: AsyncClient, token: str, username: str) -> dict[str, Any]:
    """The private profile of ``username`` as ``token`` reads it."""
    response = await client.get(
        f"/v1/users/by_id/{username}/private", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def patch_user(
    client: AsyncClient, token: str, username: str, body: dict[str, Any]
):
    """``PATCH /v1/users/by_id/{username}`` with ``body``."""
    return await client.patch(
        f"/v1/users/by_id/{username}", json=body, headers=auth(token)
    )
