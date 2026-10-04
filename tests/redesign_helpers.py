"""Shared helpers for the programme / one-off redesign tests (#384).

Everything here drives the public HTTP surface. The only direct database use
is moving a slot into the past, which has no endpoint and mirrors what the
attendance and credit suites already do.

Times are whole seconds: the server validates cutoffs and occurrence times
against RRULE expansions, which are second-granular, so a millisecond
remainder would make ``start + k * DAY_MS`` miss the boundary.
"""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
MINUTE_MS = 60 * 1000
WEEK_MS = 7 * DAY_MS

EVERY_DAY = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU"
"""A weekly rule naming every day — valid under programme R11 and gives an
occurrence every day, so a test can reach a slot without waiting a week."""

_WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def now_ms() -> int:
    """Current time in epoch milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def floor_s(ms: int) -> int:
    """Floor epoch milliseconds to a whole second."""
    return (ms // 1000) * 1000


def at(*, days: float = 0, hours: float = 0, minutes: float = 0) -> int:
    """A whole-second instant offset from now."""
    delta = timedelta(days=days, hours=hours, minutes=minutes)
    return floor_s(int((datetime.now(timezone.utc) + delta).timestamp() * 1000))


def weekday_code(ms: int) -> str:
    """The BYDAY code of the weekday ``ms`` falls on (UTC)."""
    return _WEEKDAY_CODES[datetime.fromtimestamp(ms / 1000, tz=timezone.utc).weekday()]


def auth(token: str) -> dict[str, str]:
    """Authorization header for ``token``."""
    return {"Authorization": f"Bearer {token}"}


async def create_venue(client: AsyncClient, token: str, name: str = "Rink") -> int:
    """Create a venue and return its id."""
    response = await client.post("/v1/venues", json={"name": name}, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def create_event(
    client: AsyncClient,
    token: str,
    *,
    event_type: str,
    venue_id: int,
    start: int,
    end: int | None = None,
    rrule: str | None = None,
    title: str | None = None,
    expected_status: int = 201,
    **extra: object,
) -> dict:
    """Create an event of ``event_type`` and return the response body.

    Asserts ``expected_status`` so a fixture that fails to build is reported
    where it broke rather than three assertions later.
    """
    body: dict[str, object] = {
        "title": title or f"{event_type} under test",
        "type": event_type,
        "venueId": venue_id,
        "startTimeUtc": start,
        "endTimeUtc": end if end is not None else start + HOUR_MS,
    }
    if rrule is not None:
        body["rrule"] = rrule
    body.update(extra)
    response = await client.post("/v1/events", json=body, headers=auth(token))
    assert response.status_code == expected_status, response.text
    return response.json()


async def create_programme(
    client: AsyncClient,
    token: str,
    venue_id: int,
    *,
    start: int | None = None,
    end: int | None = None,
    rrule: str = EVERY_DAY,
    **extra: object,
) -> dict:
    """Create a programme whose first occurrence is ``start`` (default +2 days)."""
    start = start if start is not None else at(days=2)
    return await create_event(
        client,
        token,
        event_type="programme",
        venue_id=venue_id,
        start=start,
        end=end,
        rrule=rrule,
        **extra,
    )


async def create_camp(
    client: AsyncClient,
    token: str,
    venue_id: int,
    *,
    start: int | None = None,
    end: int | None = None,
    count: int = 5,
    **extra: object,
) -> dict:
    """Create a daily camp of ``count`` days starting at ``start`` (default +2 days)."""
    start = start if start is not None else at(days=2)
    return await create_event(
        client,
        token,
        event_type="camp",
        venue_id=venue_id,
        start=start,
        end=end,
        rrule=f"FREQ=DAILY;COUNT={count}",
        **extra,
    )


async def create_oneoff(
    client: AsyncClient,
    token: str,
    venue_id: int,
    *,
    start: int | None = None,
    end: int | None = None,
    **extra: object,
) -> dict:
    """Create a one-off event starting at ``start`` (default +2 days)."""
    start = start if start is not None else at(days=2)
    return await create_event(
        client,
        token,
        event_type="oneOff",
        venue_id=venue_id,
        start=start,
        end=end,
        **extra,
    )


async def get_event(client: AsyncClient, token: str, event_id: int) -> dict:
    """Read one event through the admin endpoint."""
    response = await client.get(f"/v1/events/by_id/{event_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def list_occurrences(
    client: AsyncClient,
    token: str,
    from_ms: int,
    to_ms: int,
    *,
    event_id: int | None = None,
) -> list[dict]:
    """Admin occurrence listing, optionally narrowed to one event."""
    response = await client.get(
        "/v1/events/occurrences",
        params={"fromTimeUtc": from_ms, "toTimeUtc": to_ms},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    items = response.json()
    if event_id is not None:
        items = [o for o in items if o["eventId"] == event_id]
    return items


async def list_user_occurrences(
    client: AsyncClient,
    token: str,
    username: str,
    from_ms: int,
    to_ms: int,
    *,
    event_id: int | None = None,
) -> list[dict]:
    """Member occurrence listing, optionally narrowed to one event."""
    response = await client.get(
        f"/v1/myevents/by_id/{username}/occurrences",
        params={"fromTimeUtc": from_ms, "toTimeUtc": to_ms},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    items = response.json()
    if event_id is not None:
        items = [o for o in items if o["eventId"] == event_id]
    return items


async def terminate(
    client: AsyncClient,
    token: str,
    event_id: int,
    cutoff: int,
    reason: str = "Season over",
):
    """POST /terminate and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/terminate",
        json={"reason": reason, "cutoffTimeUtc": cutoff},
        headers=auth(token),
    )


async def extend(client: AsyncClient, token: str, event_id: int, cutoff: int):
    """POST /extend and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/extend",
        json={"cutoffTimeUtc": cutoff, "reason": "More ice time"},
        headers=auth(token),
    )


async def extend_indefinitely(client: AsyncClient, token: str, event_id: int):
    """POST /extend-indefinitely and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/extend-indefinitely",
        json={"reason": "Running on"},
        headers=auth(token),
    )


async def version_of(client: AsyncClient, token: str, event_id: int) -> int:
    """The event's current ``version`` (#292), for a mutation that must send it."""
    return (await get_event(client, token, event_id))["version"]


async def split(client: AsyncClient, token: str, event_id: int, **body: object):
    """PATCH /future (split) and return the raw response.

    Sends the event's current ``version`` unless the caller supplies one.
    """
    body.setdefault("version", await version_of(client, token, event_id))
    return await client.patch(
        f"/v1/events/by_id/{event_id}/future", json=body, headers=auth(token)
    )


async def cancel_series(client: AsyncClient, token: str, event_id: int, effective: int):
    """POST /cancel and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/cancel",
        json={"reason": "Weather", "effectiveDateTimeUtc": effective},
        headers=auth(token),
    )


async def occurrence_version(
    client: AsyncClient, token: str, event_id: int, slot: int
) -> int:
    """The occurrence's current ``version`` (#430), for a change that must send it."""
    response = await client.get(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["version"]


async def oneoff_occurrence_version(
    client: AsyncClient, token: str, event_id: int
) -> int:
    """The version of a one-off's single occurrence, which drop and reinstate take."""
    slot = (await get_event(client, token, event_id))["startTimeUtc"]
    return await occurrence_version(client, token, event_id, slot)


async def drop(client: AsyncClient, token: str, event_id: int, **body: object):
    """POST /drop and return the raw response.

    Sends the occurrence's current ``version`` unless the caller supplies one.
    """
    payload: dict[str, object] = {"reason": "Called off"}
    payload.update(body)
    if "version" not in payload:
        payload["version"] = await oneoff_occurrence_version(client, token, event_id)
    return await client.post(
        f"/v1/events/by_id/{event_id}/drop", json=payload, headers=auth(token)
    )


async def reinstate(
    client: AsyncClient, token: str, event_id: int, *, version: int | None = None
):
    """POST /reinstate and return the raw response.

    Sends the occurrence's current ``version`` unless the caller supplies one.
    """
    if version is None:
        version = await oneoff_occurrence_version(client, token, event_id)
    return await client.post(
        f"/v1/events/by_id/{event_id}/reinstate",
        json={"version": version},
        headers=auth(token),
    )


async def reschedule_occurrence(
    client: AsyncClient, token: str, event_id: int, slot: int, **body: object
):
    """POST /occurrences/{slot}/reschedule and return the raw response.

    Sends the occurrence's current ``version`` unless the caller supplies one.
    """
    if "version" not in body:
        body["version"] = await occurrence_version(client, token, event_id, slot)
    return await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/reschedule",
        json=body,
        headers=auth(token),
    )


async def cancel_occurrence(
    client: AsyncClient,
    token: str,
    event_id: int,
    slot: int,
    *,
    version: int | None = None,
):
    """POST /occurrences/{slot}/cancel and return the raw response.

    Sends the occurrence's current ``version`` unless the caller supplies one.
    """
    if version is None:
        version = await occurrence_version(client, token, event_id, slot)
    return await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/cancel",
        json={"reason": "Ice not ready", "version": version},
        headers=auth(token),
    )


async def undo_cancel_occurrence(
    client: AsyncClient,
    token: str,
    event_id: int,
    slot: int,
    *,
    version: int | None = None,
):
    """POST /occurrences/{slot}/undo-cancel and return the raw response.

    Sends the occurrence's current ``version`` unless the caller supplies one.
    """
    if version is None:
        version = await occurrence_version(client, token, event_id, slot)
    return await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/undo-cancel",
        json={"version": version},
        headers=auth(token),
    )


async def assign(client: AsyncClient, token: str, event_id: int, *members: str):
    """Assign members to an event and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": list(members)},
        headers=auth(token),
    )


async def enrollment_of(
    client: AsyncClient, token: str, event_id: int, member: str
) -> str | None:
    """The member's current enrollment status on ``event_id`` (admin view)."""
    response = await client.get(
        f"/v1/events/by_id/{event_id}/enrollments", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["enrollments"].get(member)


async def mark(
    client: AsyncClient,
    token: str,
    event_id: int,
    slot: int,
    member: str,
    status: str = "present",
):
    """Mark one member's attendance and return the raw response."""
    return await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{slot}/attendance",
        json={"records": [{"membername": member, "status": status}]},
        headers=auth(token),
    )


async def notifications_for(
    db_session: AsyncSession, username: str, ntype: str
) -> list:
    """Notification rows of ``ntype`` addressed to ``username``."""
    from sqlalchemy import select

    from club_server.db.models.notification import Notification

    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.username == username, Notification.type == ntype
        )
    )
    return list(result.scalars().all())


async def audit_rows(db_session: AsyncSession, action: str) -> list:
    """Audit rows carrying ``action``."""
    from sqlalchemy import select

    from club_server.db.models.audit_log import AuditLog

    db_session.expire_all()
    result = await db_session.execute(select(AuditLog).where(AuditLog.action == action))
    return list(result.scalars().all())


async def set_cutoff(
    db_session: AsyncSession, event_id: int, cutoff: int | None
) -> None:
    """Write an event's cutoff directly.

    There is no endpoint that puts a cutoff in the past (R3 forbids it), so a
    test that needs one — a programme whose cutoff has already passed — ages
    the cutoff in place, the same way the attendance suite moves an event
    into the past.
    """
    from sqlalchemy import select

    from club_server.db.models.event import Event

    event = (
        await db_session.execute(select(Event).where(Event.id == event_id))
    ).scalar_one()
    event.until_time = cutoff
    await db_session.commit()
    db_session.expire_all()


async def backdate_enrollment(
    db_session: AsyncSession, event_id: int, member: str, enrolled_at: int
) -> None:
    """Stamp ``enrolled_at`` in the past so a slot already under way is covered."""
    from sqlalchemy import select

    from club_server.db.models.enrollment import Enrollment

    row = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.event_id == event_id, Enrollment.membername == member
            )
        )
    ).scalar_one()
    row.enrolled_at = enrolled_at
    await db_session.commit()
    db_session.expire_all()
