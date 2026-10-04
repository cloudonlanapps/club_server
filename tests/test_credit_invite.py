"""Inviting is an offer; accepting is the commitment (#446, R35, R35a).

With credits on, an admin may invite a member who holds no credit yet. The
credit check sits at accept, so the invitation can be extended and simply
cannot be acted on until it is funded.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .credit_helpers import auth, create_event, create_member, open_account
from .helpers import create_admin_user

pytestmark = pytest.mark.usefixtures("credit_enabled")


async def invite(
    client: AsyncClient, admin_token: str, event_id: int, username: str
) -> None:
    """Invite ``username`` to the programme."""
    response = await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": [username]},
        headers=auth(admin_token),
    )
    assert response.status_code == 204, response.text


async def enrollment_status(
    client: AsyncClient, token: str, event_id: int, username: str
) -> str:
    """The member's own view of their enrollment on the programme."""
    response = await client.get(
        f"/v1/myevents/by_id/{username}/{event_id}/enrollments",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["status"]


async def accept(client: AsyncClient, member_token: str, event_id: int, username: str):
    """The member accepts their invitation."""
    return await client.post(
        f"/v1/myevents/by_id/{username}/{event_id}/enrollments/accept",
        headers=auth(member_token),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R35a")
async def test_should_invite_member_when_member_holds_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R35a: invitations go out before payments come in."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)

    await invite(client, admin_token, event_id, "alice")

    assert await enrollment_status(client, admin_token, event_id, "alice") == "invited"
    assert await enrollment_status(client, member_token, event_id, "alice") == "invited"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R35")
async def test_should_refuse_accept_when_invited_member_holds_no_credit(
    client: AsyncClient, db_session: AsyncSession
):
    """R35, R38: the block sits at accept, and leaves the invitation as it was."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    await invite(client, admin_token, event_id, "alice")

    response = await accept(client, member_token, event_id, "alice")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CREDIT"
    assert await enrollment_status(client, member_token, event_id, "alice") == "invited"
    assert await enrollment_status(client, admin_token, event_id, "alice") == "invited"


@pytest.mark.asyncio
@pytest.mark.requirement("credit:R43")
async def test_should_accept_invitation_once_account_is_opened(
    client: AsyncClient, db_session: AsyncSession
):
    """R43: funding the member is the only step needed to unblock the accept."""
    admin_token = await create_admin_user(db_session)
    member_token = await create_member(db_session, "alice")
    event_id = await create_event(client, admin_token)
    await invite(client, admin_token, event_id, "alice")
    refused = await accept(client, member_token, event_id, "alice")
    assert refused.status_code == 422, refused.text

    _ = await open_account(client, admin_token, "alice", credits=5, event_id=event_id)
    response = await accept(client, member_token, event_id, "alice")

    assert response.status_code in (200, 204), response.text
    assert (
        await enrollment_status(client, member_token, event_id, "alice") == "accepted"
    )
    assert await enrollment_status(client, admin_token, event_id, "alice") == "accepted"
