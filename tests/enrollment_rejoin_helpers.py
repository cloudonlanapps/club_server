"""Shared helpers for the enrollment rejoin and state tests (#465-#469).

Everything drives the public HTTP surface except ``stamp_withdrawn_at``,
which dates a past departure: there is no endpoint that withdraws a member
in the past, the same reason ``backdate_enrollment`` exists.
"""

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .redesign_helpers import auth


async def invite(client: AsyncClient, token: str, event_id: int, member: str):
    """Invite ``member`` and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": [member]},
        headers=auth(token),
    )


async def accept(client: AsyncClient, token: str, event_id: int, member: str):
    """Accept ``member``'s invitation and return the raw response."""
    return await client.post(
        f"/v1/myevents/by_id/{member}/{event_id}/enrollments/accept",
        headers=auth(token),
    )


async def request_join(client: AsyncClient, token: str, event_id: int, member: str):
    """Request to join on ``member``'s behalf and return the raw response."""
    return await client.post(
        f"/v1/myevents/by_id/{member}/{event_id}/enrollments/request",
        headers=auth(token),
    )


async def approve(client: AsyncClient, token: str, event_id: int, member: str):
    """Approve ``member``'s join request and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": [member]},
        headers=auth(token),
    )


async def assign_trial(client: AsyncClient, token: str, event_id: int, member: str):
    """Assign ``member`` a trial and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": member},
        headers=auth(token),
    )


async def remove(
    client: AsyncClient, token: str, event_id: int, member: str, **body: object
) -> Response:
    """Remove ``member`` and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/remove",
        json={"membernames": [member], **body},
        headers=auth(token),
    )


async def admin_record(
    client: AsyncClient, token: str, event_id: int, member: str
) -> dict[str, object]:
    """``member``'s enrollment record from the admin listing."""
    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    (record,) = [r for r in response.json()["records"] if r["membername"] == member]
    return record


async def member_record(
    client: AsyncClient, token: str, event_id: int, member: str
) -> dict[str, object]:
    """``member``'s enrollment as the member sees it."""
    response = await client.get(
        f"/v1/myevents/by_id/{member}/{event_id}/enrollments", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def stamp_withdrawn_at(
    db_session: AsyncSession, event_id: int, member: str, withdrawn_at: int
) -> None:
    """Date a departure in the past, as if the member left long ago."""
    from sqlalchemy import select

    from club_server.db.models.enrollment import Enrollment

    row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == member
            )
        )
    ).scalar_one()
    row.withdrawn_at = withdrawn_at
    await db_session.commit()
    db_session.expire_all()
